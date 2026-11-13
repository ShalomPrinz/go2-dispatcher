# Architecture refactor: shared context

Read this file first, in full. It is the shared background for a series of seven step prompts (`refactor-01-…md` to `refactor-07-…md`), each run in its own session, in order. The step prompt tells you what to do; this file tells you why, where the whole series is going, and the rules every step follows.

These `refactor-*.md` files are working briefs. They live only on the `refactor_prompts` branch: read them from there (`git fetch origin refactor_prompts`, then `git show origin/refactor_prompts:<file>`), never copy or commit them onto a working branch, never merge that branch, never cite them from code or docs, and never copy their wording into docs (the docs carry no process history).

## Where the work lives

All steps are committed on one branch, **`architecture_refactor`**, created from the default branch by step 1. Every step session works like this:

1. Start: `git fetch origin architecture_refactor refactor_prompts`. Put the session's working branch on the tip of `origin/architecture_refactor` before changing anything (a fresh session branch has no commits of its own, so `git merge --ff-only origin/architecture_refactor` is enough). Check with `git log --oneline | head -20` that the earlier steps are there, and read the "Step status" section below.
2. Work: commit each task locally, as the rules below say.
3. End: when the step is done and the full check list is green, push the commits to the same branch as a fast-forward: `git push origin HEAD:refs/heads/architecture_refactor`. Never force-push it, never push a red or half-done step, and do not open a pull request unless the user asks.
4. Then add the step's notes for the next session to "Step status" below, as a commit on `refactor_prompts` that changes only this file. Keep each entry short: the commits, what ended differently from what the next prompt assumes, and deferred work.

## Step status

### Step 1 (`refactor-01-contract.md`): done

On `architecture_refactor`: `tooling: add contract/ to the ownership map and agent definitions under skills-dev` and `contract: extract the shared dispatcher/skills contract into a top-level contract package`. Registry hash `768d05c6633b8fdc` before and after; 554 passed, 1 skipped; 94 integration; 6 import contracts kept.

Notes for later steps:

- `contract/` modules: `schema`, `policy`, `manifest`, `env`, `wire` (was `skills/ipc.py`), `constants` (`YAW_SIGN`, and `Link` from `skills/state_store.py`, which three dispatcher modules imported only for that type). Doc: `contract/docs/contract.md`.
- The error-code enum is `contract.schema.ErrorCode`; `SkillError` is the response's error dataclass.
- **Deferred to step 2**: `dispatcher/config.py` still imports `skills.stub` (`StubPosture`, `FaultKind`, defaults, `parse_objects`) and `skills.coco`. It did not come apart cleanly: `parse_objects` is stub implementation and the transports call `stub.reset`.
- **Candidates for `contract/` when the services move**: the service lifecycle constants (`READY_LINE`, `STARTUP_TIMEOUT_S`, `STOP_GRACE_S`, `EXIT_LOCKED`) stay in the service modules, because the dispatcher also uses those modules as launch targets.
- Import contracts now: contract imports no project package (nor `tests`); contract imports neither pydantic nor yaml except `contract.manifest`; the parser is protected (importers: `dispatcher`, `skills.tests`, `contract.tests`); the three older contracts are kept. import-linter cannot express "standard library only"; the fresh-interpreter test in `tests/integration/test_skills.py` covers the heavy imports for every `skills` module and every `contract` module except `manifest`.
- **Order that works when a step adds a package** (a commit cannot be green in between, because no shims are allowed): `claude-config-dev` first, committed alone (the ownership test requires every mapped path to exist, so it carries a self-expiring exemption for the new folder; `TYPED_ROOTS` in `.claude/hooks/typecheck.py` waits until `pyproject.toml` lists the package). Then, uncommitted and in sequence: `tests-dev` packaging in `pyproject.toml` (`lint-imports` fails until the folder exists), the package agent's move, the other package's import updates, `tests-dev` for `tests/helpers/` and the import contracts, and a `claude-config-dev` follow-up (remove the exemption, add `TYPED_ROOTS`, update the `CONTRACTS` paths in `.claude/scripts/review_preview.py`). Review and commit these together as one green commit.
- Give the first package agent a brief that says the rest of the suite will fail on imports until the next agent runs, and ask it for the full old-to-new mapping for the next brief.
- The reviewer blocks on process-history wording in docs ("was read from", "still", "moved to"). Tell agents to write decisions in the present tense with a present-tense rejected alternative.
- Known flaky test, not caused by the refactor: `skills/tests/unit/test_vision_ipc.py::test_result_and_error_round_trip` fails now and then in full runs (`EADDRINUSE`: the second `serve_once` binds before the first server thread closes its listener). It passes alone. Not fixed; the fix is a separate commit when the user agrees.
- Whole-repo `uv run basedpyright` reports 13 errors at baseline (9 missing-extra imports, 3 in `dispatcher/tests/unit/test_console_trace.py`, 1 in `tests/integration/test_live_llm.py`); compare against that, not zero.

## What this is

The system works and is tested. The refactor changes its **structure**, not its behaviour: the code has outgrown the "two packages, `dispatcher/` and `skills/`" layout. An architecture review found nine faults (below). The series fixes them in an order where each step leaves the repo green, committed and usable, so the series can stop after any step.

## The faults being fixed

File and line references were correct before step 1. Earlier steps move code, so **find things by symbol name, not by line number**.

1. **`skills/` is a flat folder holding five different things**: the dispatcher↔skill contract, the skill runtime, the five skills, the robot service with its protocol and backends, and the vision service. Only about 5 of its 27 modules are skills.
   - The "Skill processes never touch DDS" import contract in `pyproject.toml` lists skill modules by name, so a new skill is not covered until someone adds it.
   - Shared constants live in whichever module came first. `YAW_SIGN` sits in `skills/robot_ipc.py`, so the SKILL.md parser (`skills/manifest.py`) and `dispatcher/verification.py` import the IPC client for a sign table. Detector errors and `DetectResult` live in `skills/backend.py`.
2. **Stub fault injection is woven into the safety-critical motion code.** `Motion` in `skills/robot_service.py` carries `_stop_error`, `_rejected`, `_fault()`, a `sim` flag and casts to `SimRobot`. The wire protocol passes a `fault` field, and `skills/motion.py` and `skills/detect_object.py` call `stub.inject_fault()` directly.
3. **There is no backend interface.** `if backend_name() == "real"` is repeated in `skills/local.py` (four times), `skills/robot_service.py` (`start_source`, `start_motion`), `backend.sleep` and the vision service.
4. **`dispatcher/executor.py` is a god class.** `Executor` builds the child environment, spawns skills, tracks kills, pings for liveness, runs the stop path and its fallback, manages the control connection, runs utilities, and owns both service supervisors. Methods return `(StopMoveResult, bool)` tuples; `service_lost` exists on both `ExecResult` and `StopMoveResult`; `dispatcher.py` copies `ExecResult` into `StepResult` field by field; `RobotService` (`dispatcher/robot.py`) and `VisionService` (`dispatcher/vision.py`) duplicate about 80 lines of spawn, log and terminate code.
5. **The dispatcher knows skills by name.** `needs_vision` (`executor.py`) compares the entrypoint to `skills.detect_object`; `report.py` hardcodes `MOTION_SKILLS = {"walk","turn","sit","stretch"}`; `verification.py` repeats the `"sdk_error"` string. `skills.dir` is configurable, so these belong to the skill's own declaration.
6. **`dispatcher/llm.py` holds two unrelated jobs**: the plan contract (tool schema, `validate_tool_input`, semantic checks) and two provider clients with the retry loop. The schema's status enum is hand-duplicated from `Plan.status`. `context.py` imports `llm`, and `llm` imports `context` back under `TYPE_CHECKING`, because `PromptSurface` lives in the wrong place.
7. **The run log is a 15-method protocol implemented five times** (`RunLog`, `NullLog`, `TeeLog`, `ConsoleTrace`, `TelegramProgress`). A new record type touches five classes plus the Protocol. `llm_retry` already passes a bare `dict`.
8. **Layering inversions in the dispatcher.** `models.py` imports `prompts`; `config.py` imports `skills.stub` and `skills.coco`; `prompts.py` mixes hash-affecting model texts with operator, Telegram HTML and help texts; the composition root lives in `transports/__init__.py` and reaches through with `executor.vision.start()`; `cli.py` builds a whole `Executor` just to read state.
9. **Task ending in `Dispatcher` is more machinery than it needs.** `_Ending` is unpacked field by field into `_end()`, the phase/stop state machine is interleaved with the loop, and `Dispatcher` builds its own `RunLogFactory`. Lowest priority; the loop itself reads well.

## Target layout

```
contract/    schema, policy, manifest, env vars, error codes, domain constants, shared wire framing
robot/       service (server, lease/motion, state store), protocol, client,
             backends/{real, sim, faults}, utilities (stop_move, read_state, lease)
vision/      service, protocol/client, detectors/{real, stub}, coco, box classification
skills/      runtime (runner, process, motion helpers) + one folder per skill (module + SKILL.md)
dispatcher/
  planning/    plan model, tool schema, validation, bounds, budget, verification
  llm/         planner protocol, retry, anthropic, openrouter
  execution/   skill process, stop path, service supervisors (shared base)
  runlog/      events, writer, observers
  transports/  cli, telegram
  app.py       composition root
analysis/    report (it only reads logs)
```

Dependency direction, enforced by import-linter contracts at package level (no module-name lists):

- `contract` imports no other project package. Everything outside `contract.manifest` is standard library only.
- `robot` and `vision` import `contract`, never each other's internals, never `skills` or `dispatcher`.
- `skills` imports `contract` and the `robot` / `vision` **clients** only: never a backend, a service module, `unitree_sdk2py`, `cyclonedds`, the detector stack, pydantic or yaml.
- `dispatcher` imports `contract`, the clients, and what it needs to start the services (module names, lifecycle constants): never a backend.
- `analysis` imports only what it needs to read logs.

## The steps

| # | Prompt | Result | Faults |
|---|---|---|---|
| 1 | `refactor-01-contract.md` | `contract/` package; no more odd imports for shared constants | 1 (part), 5 (`sdk_error`) |
| 2 | `refactor-02-robot-vision-packages.md` | `robot/` and `vision/` packages, moved as they are; package-level import contracts | 1 |
| 3 | `refactor-03-backend-interface-faults.md` | one backend interface; fault injection out of `Motion` and off the wire | 2, 3 |
| 4 | `refactor-04-skill-declarations.md` | one folder per skill, `skills/runtime/`; skills declare what the dispatcher used to know by name | 1 (rest), 5 |
| 5 | `refactor-05-planning-llm.md` | `dispatcher/planning/` and `dispatcher/llm/`; model texts split from operator texts | 6, 8 (part) |
| 6 | `refactor-06-execution-app.md` | `dispatcher/execution/`, `dispatcher/app.py`, `analysis/` | 4, 8 (rest) |
| 7 | `refactor-07-runlog-events.md` | `dispatcher/runlog/` with typed events and one `emit`; simpler task ending | 7, 9 |

Each step assumes the earlier ones are done. **The repo wins over these files**: at the start of a step, check the actual layout (`ls`, `git log --oneline | head -20`, `docs/architecture.md` "Repository layout"). If an earlier step is missing or ended differently from what the step prompt assumes, tell the user before doing anything.

## Invariants: what must not change in any step

A step that cannot keep one of these stops and asks the user.

- **The registry hash and every golden file** (`dispatcher/tests/golden/`). Capture the hash before you start (`uv run go2 catalog`, last lines) and compare at the end. The hash covers the system text, the catalog text and the tool schema; the `entrypoint` and other unrendered frontmatter are not part of it, which is what lets skill modules move. No fixed text the model sees and no rendered `SKILL.md` content changes.
- **The run log on disk**: file names, envelope, record types and fields, `index.jsonl` (`dispatcher/docs/run-log.md`). Logs written before and after the refactor must be analysable together.
- **The skill response schema, error codes and outcome codes.**
- **The stop path and every other safety behaviour** in `docs/safety.md`: one stop path, the 500 ms fallback, lease semantics, orphan handling, the single-instance lock, timeouts. Moving this code is allowed; changing what it does is not. Treat every diff that touches it as safety-critical and have the reviewer look at it specifically.
- **Config keys, CLI commands and exit codes, Telegram commands and replies.**
- **Offline by default**: the default test suite needs no SDK, API key, network or robot; SDK, CycloneDDS and vision libraries stay lazily imported.
- The robot-service wire protocol stays as it is except where a step prompt says otherwise (step 3 removes the stub-only `fault` field).

Tests are the evidence that behaviour is preserved. Move and re-target tests with the code; do not delete a test because its target moved, and do not weaken an assertion to get green. A deleted test needs a stated reason in the agent's report.

## Rules for every step

Root `CLAUDE.md` applies in full. The points that matter most here:

- **You are the orchestrator.** Delegate every code and doc change to the owning project agent with the `delegate` skill, one task at a time. You edit only the docs listed under `main` in `.claude/ownership.json` (`docs/README.md`, `docs/architecture.md`, `docs/project.md`, `docs/references.md`). Verify with commands only; do not read the whole doc set or source yourself.
- **Safety rules are absolute**: stub backend only; never `--backend real`, never `--run-robot`; no `--run-live` and no `go2 run` (paid API) in this series.
- **One commit per task**, each green: `uv run ruff format .`, `uv run ruff check .`, `uv run lint-imports`, `uv run basedpyright <changed files>`, `uv run pytest -q`, and `uv run pytest -m integration` for anything that touches processes, services or the stop path. Run the `reviewer` agent on the uncommitted diff before each commit, then commit with the `git-commit` skill.
- **Docs move with the code, in the same commit.** A moved module's contract moves to the doc of its new package; code comments cite the new doc path. No doc and code may disagree at any commit.
- **Design decisions are recorded in the owning doc** with the reason and the rejected alternative. The reversal of the "Two services, `dispatcher/` and `skills/`" decision in `docs/architecture.md` is yours to write (step 1 starts it, each later step keeps "Design decisions", "Repository layout" and the component description current). `docs/README.md` tables are also yours.
- **Clean design over small diff.** Do not leave re-export shims, compatibility aliases or "moved to" stubs behind. A rename updates every importer, test, doc and comment.
- **Ask before an architecture decision** that this file or the step prompt does not already settle. Each step prompt lists the decisions it expects; ask them in one question at the start, with your recommendation, then wait.
- **Scope discipline.** A step fixes its own faults. If an agent finds something that belongs to a later step, it reports it and leaves it. If it finds a real bug, it reports it; the fix is a separate commit, with a test, only after the user agrees.

### Decisions already taken for the whole series

Follow these; if one turns out to be wrong, stop and raise it instead of working around it.

- **Every top-level package is laid out like the existing two**: the folder is the Python package, with its own `docs/`, `tests/` and `CLAUDE.md` (commands, doc table, gotchas; links, no `@` paths). Cross-package tests stay in root `tests/`.
- **No new dev agents.** `skills-dev` owns `contract/`, `robot/`, `vision/` and `skills/`; `dispatcher-dev` owns `dispatcher/` and `analysis/`. `tests-dev` keeps `pyproject.toml` (packaging, import contracts, type-check and coverage scope) and the test infrastructure. Left out: a separate robot-side agent; the signal to add one is `skills-dev` briefs regularly needing more context than one agent run holds.
- **Whenever a step adds or renames a top-level package**, three things happen before any code moves, each by its owner: `claude-config-dev` updates `.claude/ownership.json` (`agents` paths and `packages`), the agent definitions, and any hook or script that names package paths (for example the safety-file list in `.claude/scripts/review_preview.py`); `tests-dev` updates `pyproject.toml` (`tool.hatch` packages and excludes, `pytest` testpaths, `coverage` source, `importlinter` root packages and contracts, `basedpyright` include); you update `docs/architecture.md` and `docs/README.md`.
- **A contract owned by `contract/` is defined before its users change**: when a task touches several packages, split it per agent and run the defining side first.

## At the end of every step

Report to the user, briefly:

1. Commits made (one line each) and the final results of the full check list above, including the registry hash before and after.
2. Faults closed, and anything in the step's scope that was left open, with the reason.
3. Decisions taken and where they are recorded.
4. Complexity deliberately left out and the signal that would justify adding it.
5. **Notes for the next step**: anything that ended differently from what the next prompt assumes (names, locations, deferred work). The user carries these into the next session.
