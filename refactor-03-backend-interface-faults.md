# Step 3 of 7: one backend interface, and fault injection out of the motion code

Read `refactor-00-context.md` first (same branch as this file: `git show origin/refactor_prompts:refactor-00-context.md`). It holds the background, the target layout, the invariants and the working rules for this whole series. Then read root `CLAUDE.md`, `docs/README.md` and `docs/safety.md`.

## Goal

Two changes inside `robot/` and `vision/`, which step 2 created by moving code as it was:

1. **A backend interface.** The choice between the real robot and the simulation is made once, at start-up, and returns an object. No `if backend_name() == "real"` branch remains anywhere else. Adding a third backend means adding one module and one registration.
2. **Fault injection leaves the safety-critical path.** `Motion` (lease, primitives, stop) has no knowledge of faults, of the simulation, or of which backend it runs on. The wire protocol has no `fault` field. Faults that simulate a misbehaving robot are produced by a wrapper around the sim sport client; faults that simulate a misbehaving skill process are produced in one place in the skill runtime.

This is the step with the most safety value, and the only one in the series that deliberately changes code on the stop path. Everything observable on the real backend must stay identical.

## Starting point

Find these by symbol; paths are as step 2 left them.

- Backend branches: the utilities' robot access (formerly `skills/local.py`: `init_dds`, `setup_timing`, `sport_client`, `sample_state`), the robot service's `start_source` and `start_motion`, the backend `sleep` (stub time scale), and the vision service's detector choice.
- `Motion` in the robot service: `_stop_error`, `_rejected`, `_fault()`, the `sim` property, casts to `SimRobot`, and the `fault` argument of `move` and `action`.
- `_service_fault` in the robot service reads `fault` from the request; the skills' motion helpers (`motion.py`) and `detect_object.py` call `inject_fault()` and pass the result on the wire.
- The stub's fault code (`_FaultInjector`, `inject_fault`, `apply_service_fault`): the fault kind comes from an environment variable set per skill process; only the process's first action is faulted. Kinds fall in two groups: the skill process misbehaves (hang, crash, garbage on stdout) and the robot or service misbehaves (error, rejection, stop error, service crash, service hang). Read the stub and fault-injection doc for the exact list and semantics before designing.
- Any import-contract exception that step 2 added for the dispatcher's direct use of the stub (defaults, `reset`).

## Requirements for the design

- **Backend interface** in `robot/backends/`: a protocol covering what the service and the utilities need (state source, sport client, one-shot state sample, set-up timing, the clock or sleep that carries the stub time scale), plus one loader that maps the configured name to an implementation. The real backend's third-party imports stay lazy. The vision detectors get the same treatment at their own size (a detector factory; do not build a framework).
- **`Motion` is backend-agnostic**: it receives a sport client and a state store, and that is all it knows.
- **Robot-side faults** live in `robot/backends/faults.py` as a wrapper around the sim sport client (and, for service crash and hang, around whatever the wrapper needs to reach). It is constructed only by the sim backend; the real backend cannot load it. An import contract or a test enforces that.
- **Skill-process faults** (hang, crash, garbage) are triggered from one hook in the skill runtime, not from each skill or helper.
- **The per-step fault still reaches the right place.** Today the skill process reads the environment variable and forwards service-side kinds on the wire. With the wire field gone, the wrapper needs another way to learn the fault for the current step. Candidates: the stub state file, which is already the simulation's shared cross-process state; a sim-only control message handled by the wrapper's side of the service, never by `Motion`; the lease-arm call. Recommended starting point: the stub state file, because it keeps the protocol identical on both backends. The agent researches and proposes; the user decides.
- **Same observable fault behaviour**: every fault kind produces the same skill response, verdict, outcome and log records as before, at the same point in the step (for example the mid-walk fault still fires while a velocity command is active). `stub.faults` config and the `--fault` CLI option keep their meaning.
- **The dispatcher stops importing backends**: stub defaults it needs for config validation come from `contract/` or the `robot` client surface, and resetting the stub goes through a function on that surface. Remove the step 2 contract exception.

## Out of scope

Reshaping `skills/` (step 4); anything in `dispatcher/` beyond the imports and calls named above; new fault kinds; new backends; changes to the real backend's SDK calls, settle waits or timing.

## Decisions to confirm with the user before implementing

This step is design work. Do not start implementation on your own judgement.

1. Delegate a **read-only design task** to `skills-dev`: the backend protocol (names and signatures), the loader, the fault wrapper, the channel that carries the per-step fault, the one hook for skill-process faults, and a table of every fault kind with where it is applied before and after. The proposal lists what it leaves out.
2. Present that design to the user with your recommendation and the rejected alternatives. Wait for approval.

## Tasks, in order (after approval)

1. **You**: baseline (default and integration suites, `lint-imports`, registry hash). Note the integration tests that exercise each fault kind; they are the regression net.
2. **`skills-dev`**: the backend interface for the robot (service and utilities), with tests. No fault changes yet. Commit.
3. **`skills-dev`**: the vision detector selection. Commit.
4. **`skills-dev`**: the fault wrapper, the runtime hook, `Motion` cleaned, the `fault` field removed from the protocol, protocol and fault docs updated, tests moved to target the wrapper. Commit. If the protocol doc lists message fields, the removal is recorded there with the reason in "Design decisions".
5. **`dispatcher-dev`**: remove the dispatcher's backend imports (config defaults, stub reset); `tests-dev`: remove the contract exception and add the "real backend never loads faults" rule.
6. **You**: `docs/architecture.md` (the "stub replaces only the SDK layer" decision gains the interface and the wrapper; components), `docs/safety.md` only if it names moved code (it is owned by the dev agents, so delegate the edit).

For the reviewer on tasks 2 and 4: ask it to trace the real-backend path through `stop`, `halt`, lease expiry, connection close and the fallback stop, and to confirm line by line that the only differences are the removed fault hooks and the injected backend object.

## Done when

- `grep -rn "backend_name()" robot vision skills dispatcher --include=*.py` finds only the loader (and tests).
- `Motion` contains none of: `fault`, `sim`, `SimRobot`, `_stop_error`, `_rejected`.
- `grep -rn "inject_fault\|\"fault\"" skills robot/` shows no skill or helper calling the injector and no `fault` key in protocol code.
- `uv run lint-imports` passes with no dispatcher-to-backend exception.
- Default and integration suites pass, including every fault-injection test; the registry hash and golden files are unchanged.

Finish with the end-of-step report described in the context file. Under "left out", name at least: a plug-in registry for backends, and fault injection on the real backend.
