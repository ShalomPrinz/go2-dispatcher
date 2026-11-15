# Architecture

How the system is put together and why. Behaviour details live in the topic documents linked below; the code is the reference for everything it states unambiguously.

## Overview

An operator sends a natural-language task over Telegram or the CLI. The LLM returns a **plan**, an ordered list of skill calls, through a single tool, `submit_plan`. The dispatcher validates the plan, runs each step as a separate subprocess that drives the Go2 (or a stub), and feeds structured results back. The model then answers `DONE`, `PLAN` (more steps), `ABORT` or `ASK` (a clarifying question that ends the task). Budgets and limits bound every task, and every task writes a JSONL run log.

## Components and data flow

```
 Operator ──► Transport (Telegram | CLI)
                │  run_task(text) / request_stop() / shutdown()
                ▼
           Dispatcher ─────────────────────► Run log (logs/<provider>/<backend>/*.jsonl, index.jsonl)
            │    ▲
   context  │    │ plan
            ▼    │
       LLM client (Anthropic Messages API, single tool submit_plan)
            │
            ▼
  Plan validation ─► bounds ─► motion budget   (whole plan, before any step runs)
            │
            ▼
        Executor ── one subprocess per step ──► python -m skills.<skill>
            ▲                                          │ state reads, motion primitives
            │   one JSON line (skill response)         │ (only under the step's lease)
            └──────────────────────────────            ▼
            │
            ├── starts, arms, stops, pings ───► python -m services.robot.service
            │   (control connection)            (owns robot state and motion; real SDK | sim)
            ├── fallback stop ────────────────► python -m services.robot.stop_move (fresh process)
            └── starts, restarts, stops ──────► python -m services.vision.service
                                                (detect_object asks it over a Unix socket)
```

| Component | Code | Owner doc |
|---|---|---|
| Transports: CLI (`go2`) and Telegram (`go2 bot`), both over the same dispatcher | `dispatcher/transports/` | [running.md](running.md) |
| Dispatcher loop, busy-reject, stop, shutdown | `dispatcher.py` | [loop-and-context.md](../dispatcher/docs/loop-and-context.md), [safety.md](safety.md) |
| Context assembly and the fixed texts the LLM and operator read | `context.py`, `prompts.py` (model texts), `operator_messages.py` (operator texts), `transports/texts.py` (transport replies) | [loop-and-context.md](../dispatcher/docs/loop-and-context.md) |
| LLM layer: planner protocol, prompt surface, infra retries, one module per provider | `llm/` | [llm.md](../dispatcher/docs/llm.md) |
| Plan model, tool schema, plan validation, parameter bounds, motion budget, verification | `planning/` | [loop-and-context.md](../dispatcher/docs/loop-and-context.md), [safety.md](safety.md) |
| Skill registry and generated catalog; `SKILL.md` schema and parser | `registry.py`, `contract/manifest.py`, `skills/*/SKILL.md` | [skills.md](../skills/docs/skills.md), [contract.md](../contract/docs/contract.md) |
| Execution: skill subprocesses, timeouts, kill, leases, control connection, stop path and fallback, state reads | `execution/` (`executor.py`, `process.py`, `kill.py`, `control.py`, `stop_path.py`, `outcome.py`, `env.py`) | [execution.md](../dispatcher/docs/execution.md), [safety.md](safety.md) |
| Vision service lifecycle (start, restart before a skill that declares `uses_vision`, stop) | `execution/vision_service.py`, `services/vision/service.py` | [running.md](running.md), [vision.md](../services/vision/docs/vision.md#vision-service) |
| Robot service lifecycle, state reads, motion primitives and lease (start, task-start read, state stream, stop, restart after loss) | `execution/robot_service.py`, `execution/state_stream.py`, `execution/control.py`, `services/robot/service.py` | [running.md](running.md), [service.md](../services/robot/docs/service.md#robot-service) |
| Skill runtime, skill-process faults and the skills | `skills/` | [skills.md](../skills/docs/skills.md) |
| Robot protocol and client, backend interface and loader, real and sim backends, robot-side fault wrapper, stub reset, utilities (`stop_move`, `read_state`), posture rule | `services/robot/` | [service.md](../services/robot/docs/service.md), [robot.md](../services/robot/docs/robot.md) |
| Shared backend code: stub state file, DDS init | `services/backend/` | [backend.md](../services/backend/docs/backend.md) |
| Vision protocol and client, real and stub detectors, box classification | `services/vision/` | [vision.md](../services/vision/docs/vision.md) |
| Shared contract: skill response schema and error codes, policy types, process environment, wire framing, backend selection, process helpers, generic process runtime, fault kinds and the stub fault file, stub world format, shared constants | `contract/` | [contract.md](../contract/docs/contract.md) |
| Run log and index | `runlog.py` | [run-log.md](../dispatcher/docs/run-log.md) |
| Configuration and `.env` | `config.py` | [configuration.md](configuration.md) |
| Single-instance lock | `process_lock.py` | [safety.md](safety.md) |

One task, in outline: build the context from fixed slots → call the LLM → validate the plan and pre-check every step → run steps up to the model's checkpoint (or all of them), one subprocess each → call the LLM again with the reason (`plan_complete`, `checkpoint` or `failure`) → repeat until `DONE`, `ABORT`, `ASK`, or a budget or limit ends the task. The full loop is in [loop-and-context.md](../dispatcher/docs/loop-and-context.md).

## Process model

- **One dispatcher process per machine**, enforced by a file lock, so two processes can never drive one robot ([safety.md](safety.md)).
- Inside it, the transport thread receives messages and one task runs at a time. While a task runs, other messages get a busy reply (no queue); only the stop word gets through. The Telegram bot handles updates concurrently so that `stop` can arrive during a task.
- **One subprocess per skill call** (`python -m skills.<skill> '<json params>'`), in its own process group, never reused; skill processes never touch DDS. It prints exactly one JSON response line and exits. Two utilities, `stop_move` and `read_state`, run the same way but are not skills; they live in the `robot` package and keep their own robot access through the robot backend object (`services.robot.backends.load()`), so the fallback stop works while the robot service is hung or dead.
- **One long-running vision service** (`python -m services.vision.service`), started by the dispatcher and restarted before a step of a skill that declares `uses_vision` if it has died. It loads the detector once and keeps the camera client open; `detect_object` stays a one-shot skill process that asks it over a Unix socket. It never commands motion, so it is outside the stop path ([running.md](running.md), [vision.md](../services/vision/docs/vision.md#vision-service)).
- **One long-running robot service** (`python -m services.robot.service`), started by the dispatcher and the only long-lived owner of robot state and motion. It keeps the latest `rt/sportmodestate` message and one `SportClient` (on the stub, `SimRobot`), and answers over a Unix socket at a fixed path in `log.dir`, guarded by a lock file. Skills sample `state_before` and `state_after` through it and ask it for motion primitives, which it runs on its own 10 Hz loop; the dispatcher reads fresh state through it before each task's first plan. The dispatcher holds its single control connection and arms each step with a lease token and deadline; the service moves the robot only for the armed step's connection, and sends `StopMove` when that connection closes, the deadline passes, or the control connection drops ([service.md](../services/robot/docs/service.md#robot-service), [safety.md](safety.md)).
- The robot is stationary while the LLM is called; motion happens only inside skill processes.
- The **dispatcher never imports the SDK or touches DDS**. It imports skill modules only to read their policy objects (timeout formula, motion cost, observations shown to the model), and importing a skill module never imports the SDK.
- If the dispatcher dies, its control connection closes and the robot service stops the robot ([safety.md](safety.md)).

## Why a purpose-built dispatcher

The predecessor ran on OpenClaw, a general agent runtime. OpenClaw is good at connectivity: channels, sessions, plumbing. It was replaced as the runtime for three reasons.

1. **Measurement validity.** OpenClaw's context grows with the session and is mostly content outside experimental control, which affects a dependent variable directly: tokens. In the predecessor's logs each call carried 27-58k input tokens, against 1.2-2.0k per call here (measured on other models, and a tuned OpenClaw will carry less), and first-turn input grew from about 27k to about 32k over a session ([project.md](project.md#what-the-predecessors-logs-show)). OpenClaw can also compact context at times that depend on session history, but those logs do not show this as a problem on the cloud model: 0 compactions in 98 Sonnet runs, with a context limit of 1,048,576 tokens. It compacted in 6 of 7 runs on a local model. Latency did not follow input tokens in those logs, so this reason concerns control of the context and token figures, not a shown latency effect.
2. **No grounding layer to lose.** OpenClaw does not check an LLM's claims against reality, so verification had to be built either way.
   - Tool-loop detection (`tools.loopDetection`) is repetition-based and disabled by default.
   - The `tool_result_persist` hook only transforms results before they are stored.
   - The audit ledger holds metadata only (no arguments or results), so it cannot serve as an experiment log.
3. **The plan as an object.** Under a generic tool-use loop the plan is implicit in a sequence of calls and cannot be logged, counted or inspected.

What was lost, and how it is covered:

| Lost from OpenClaw | Coverage here |
|---|---|
| Provider retry | Own infrastructure retries with backoff, logged separately, never counted as LLM calls or replans ([llm.md](../dispatcher/docs/llm.md)) |
| Model failover | Not covered; accepted |
| Free-text replies to the operator | `message` field, required on `DONE` / `ABORT` / `ASK`, optional on `PLAN` ([loop-and-context.md](../dispatcher/docs/loop-and-context.md)) |
| Telegram integration | Own thin integration on `python-telegram-bot` ([running.md](running.md)) |
| Compaction | Removed deliberately: the context is fixed-slot and depends only on the current task |

OpenClaw remains in the project as a system-level baseline for the study ([project.md](project.md#openclaw-as-a-system-level-baseline)).

## Design decisions

Cross-cutting decisions. Decisions that belong to one topic are in that topic's document.

- **Measurement validity comes first.** Anything that would make tokens, latency or replanning depend on something other than the task and the experimental condition is avoided: no conversation history, no compaction, no prompt caching, thinking at its lowest setting, fixed notice texts identical across conditions. The individual decisions are in [loop-and-context.md](../dispatcher/docs/loop-and-context.md) and [llm.md](../dispatcher/docs/llm.md).
- **The plan is a first-class object, returned through one tool (`submit_plan`).** It can be validated, logged, counted and inspected, and every LLM call has one output contract. Rejected: a generic multi-tool loop, where the plan exists only implicitly.
- **Context is rebuilt from fixed slots on every call, not carried as a conversation.** Context size depends only on the current task, which keeps measurements comparable across conditions. Static content (tools, system text, catalog) comes first so prompt caching could be added later without reordering. Layout in [loop-and-context.md](../dispatcher/docs/loop-and-context.md).
- **Robot state and motion come from one long-lived service, not from each skill process.** DDS init, discovery and the first message cost about 0.2 s in every skill's state sample and `SportClient` init about 0.7 s in every motion skill, and a fresh process on an unstable link can fail to create a DDS domain at all; a participant that stays up avoids both and keeps state available between steps. Primitives run in the service, so skills keep their policy but never stream `Move`; the lease makes "stationary during LLM calls" a rule the service enforces, not only the dispatcher. The socket path is fixed so one robot owner exists per machine and other readers can attach. Rejected: a per-PID socket as vision uses (nothing can attach, and two dispatchers could each start one); separate state and motion services (two lifecycles and participants with no measured need); shared memory, ZeroMQ or HTTP for the IPC (dependencies or complexity for a sub-millisecond gain).
- **One subprocess per skill call.** A hung skill can be killed, and the lease ends with its connection, so a killed or crashed skill stops the robot even if the dispatcher's `stop` is lost. A native crash in the robot service is covered by the fallback stop and a service restart. Rejected: skills in-process inside the robot service (saves about 0.08 s per step but gives up killing a hung skill).
- **Vision is the one exception: a long-running service, not loaded per call.** Loading torch, YOLO and the camera client cost about 5 s in every `detect_object` process on the robot, while a warm predict takes 0.04 s. The service only reads the camera, so a fresh DDS channel per call buys no safety there. Rejected: an ONNX export without torch, which keeps per-call processes but saves only about 2 s and leaves the camera client setup.
- **One stop path** for operator stop, step timeouts, the task time limit, shutdown and internal errors: kill the skill's process group and send `stop` on the robot service's control connection; if no `stop_sent` arrives within 500 ms (tunable), SIGKILL the service and send `StopMove` from a fresh `stop_move` process, the permanent fallback. One tested mechanism; a killed process cannot clean up after itself, and the warm stop avoids DDS and client init on the stop path ([safety.md](safety.md)). Rejected: a fresh-process `StopMove` only (about 1 s, and it failed to create a DDS domain on an unstable link); a separate stop client in the service (replies are matched per request id and `Move` never waits for one, so a stop is never queued behind motion).
- **Verification lives in the dispatcher, separate from the skills.** Each step's sampled state is checked by universal fault detection and, when the step's outcome is `ok`, by the skill's declared `expect`; a `failed` verdict drops the rest of the plan and calls the model again. A fresh pre-step read checks the step's `requires`. Only a skill declared `self_reported` (`detect_object`) is judged by its own status. Of the state, the model sees only the derived posture and `obstacle_m`; a detected fault or a missed goal reaches it as a fixed text on the step line ([loop-and-context.md](../dispatcher/docs/loop-and-context.md#verification)).
- **The stub replaces only the SDK layer**, so process start, timeouts, kills, the lease and both stop paths are exercised for real in offline development and tests ([service.md](../services/robot/docs/service.md#stub-backend)). The choice is made once per process: the robot side loads a `RobotBackend` (`RealBackend` or `SimBackend`) through one loader, the vision side picks its detector through one factory, and both select by name through `contract.backend.select`, so no other code branches on the backend name and a further backend is one module and one table entry ([service.md](../services/robot/docs/service.md#robot-backends)). Stub faults stay off the safety-critical path: robot faults come from a wrapper around the sim sport client that only the sim backend builds, armed from a stub-only fault file the dispatcher writes before each step's `arm`, so `Motion`, the wire protocol and the real backend carry no fault code; skill-process faults fire from one hook in the skill runtime ([service.md](../services/robot/docs/service.md#fault-injection), [skills.md](../skills/docs/skills.md#skill-faults)). Rejected: name branches at each use of the backend; fault flags inside `Motion` and a `fault` field on the wire, which put stub-only code on the stop path of the real robot; a plug-in backend registry, which two backends do not need.
- **The target deployment is a computer mounted on the robot running the whole system**, wired to it by one Ethernet port and reaching the LLM API and Telegram over Wi-Fi. Until that computer is in place, the lab machine in [setup.md](setup.md) is used ([roadmap.md](roadmap.md#pending-human-work)). This keeps DDS on the robot's wired subnet. Rejected: a lab PC on Wi-Fi with skills launched on the robot's Jetson over SSH, or a DDS relay over Wi-Fi; both put the robot link and the stop path on Wi-Fi. A physical stop must be in reach at all times ([safety.md](safety.md)).
- **Runs offline by default.** Stub mode and the default test suite need no SDK, API key, network or robot; SDK, CycloneDDS and vision libraries are imported lazily, only on real-backend paths. `uv` and `pyproject.toml`, no `sys.path` changes ([setup.md](setup.md)).
- **Every starting value is a config key or a named constant**, never inline, because almost all of them are expected to be tuned ([configuration.md](configuration.md)).
- **Simple, inspectable mechanisms** over clever ones (for example fixed settle waits rather than motion detection, commanded rather than estimated motion budget), matching the project's scope and the student's background.
- **Four top-level packages: `contract/`, `services/`, `skills/` and `dispatcher/`. `services/` holds the two service packages, `services/robot/` and `services/vision/`; each package holds its own source, `docs/` and `tests/`.** `contract` holds what the others agree on (the skill response schema and error codes, the `SKILL.md` schema, the policy types, the process environment, the shared wire framing, backend selection, the process helpers, the generic runtime that skills and the robot utilities share, the stub fault kinds, the simulated world's format and defaults, and domain constants) and imports no other project package. `services.robot` holds the robot service, its protocol and client, the backend interface with the real and sim backends, the robot-side fault wrapper, the stub reset and the two utilities; `services.vision` holds the vision service, its protocol and client, and the real and stub detectors. `skills` holds the skill runtime `skills.runtime` (`run_skill`, the skill-process fault hook, the motion helpers) and one package per skill. Each service package has a small client surface, the only modules another package imports: `services.robot.{protocol, client, ramp, stub}` and `services.vision.{protocol, client}` ([service.md](../services/robot/docs/service.md#design-decisions), [vision.md](../services/vision/docs/vision.md#design-decisions)). `skills` imports `contract` and the client surfaces; `dispatcher` imports `contract` and the client surfaces (protocol types, lifecycle constants, the service module names) and takes the fault kinds, stub defaults and COCO class names from `contract`, and never a detector. It imports no skill module: whether a skill needs the vision service or moves the robot comes from that skill's `SKILL.md` declarations (`uses_vision`, `moves`) through the registry ([contract.md](../contract/docs/contract.md#skillmd-frontmatter)). It names no backend and imports no utility module: it starts the two utilities by the module name and policy in the robot protocol's descriptors (`STOP_MOVE`, `READ_STATE`), and resets the stub through `services.robot.stub`; `services.robot` and `services.vision` import `contract`, never `skills` or `dispatcher`, and never each other. Import-linter contracts enforce these directions at package level, so a new skill module is covered without naming it, and none of them carries an exception ([testing.md](../tests/docs/testing.md#import-boundaries)). `services/` groups the two long-lived processes the dispatcher supervises, each with a protocol, a client, backends and a service, and is the one place for what both services share, such as the simulated world and the DDS connection they both sit on. Each service keeps its own protocol and client: the two message sets differ, and the common framing is `contract.wire`, which skills and the dispatcher also use. Backend selection and the process helpers stay in `contract`, because skills import them. That shared code is the `services.backend` package (the stub state file and the one DDS init per process); both services import it, it imports neither service, and skills and the dispatcher never import it ([backend.md](../services/backend/docs/backend.md#design-decisions)). `services/` itself holds only that package, the two service packages, its `CLAUDE.md` and an empty `__init__.py`. With each service in its own package, the boundary a skill process must not cross (a backend, the SDK, the detector stack) is a package boundary, and the safety-critical robot code has one home. Tests that need more than one package live in a root `tests/` folder ([testing.md](../tests/docs/testing.md#design-decisions)). A small shared root `docs/` holds what covers the whole system (project, architecture, safety, configuration, setup, running, roadmap, references); package docs live in the package's `docs/`. Rejected: two packages, `dispatcher/` and `skills/`, with the contract inside `skills/`; the contract modules sit among the skills, services and backends, a shared constant lives in whichever module needs it first, and the dispatcher imports implementation modules to read it. Rejected: `robot/` and `vision/` as two top-level packages; the top level then mixes the system's parts with two members of one kind, and code both services share has no home. Rejected: a shared protocol or client module in `services/`; the two protocols are different message sets and the shared framing is already `contract.wire`. Rejected: three packages, with the services and backends inside `skills/`; a flat folder holds the skills, both services and the backends, and the rule that skill processes never touch DDS has to list skill modules by name.
- **Inside `dispatcher/`, the plan contract and the LLM layer are separate sub-packages that never import each other.** `dispatcher.planning` holds what a valid plan is (the plan model, the `submit_plan` tool schema, schema and semantic validation, parameter bounds, the motion budget, verification); `dispatcher.llm` holds how a request is sent and a reply returned (the planner protocol, the prompt surface, the retry loop, one module per provider). The loop is the one place that asks a planner for a reply and then asks `planning` to check it. The fixed texts are split by audience: `prompts.py` holds the model's fixed texts (the system text feeds the registry hash), `operator_messages.py` the operator outcome texts (which also reach the model through the previous-task slot), and `transports/texts.py` the transport replies; the fixed-texts golden file covers all three. Import-linter contracts enforce the separation ([llm.md](../dispatcher/docs/llm.md#design-decisions), [loop-and-context.md](../dispatcher/docs/loop-and-context.md#design-decisions)). Rejected: one `llm` module holding both the plan contract and the provider clients, where a provider change and a validation change touch the same module and the context builder and the clients import each other.
- **The package folder is the Python package** (`contract`, `dispatcher`, `services.robot`, `services.vision`, `skills`), with `docs/` and `tests/` inside it and excluded from the wheel. Rejected: a `src` layout (packages under a top-level `src` folder), which puts unrelated packages under one generic folder and separates them from their tests and docs; and a nested `dispatcher/dispatcher/` package folder, which adds a level without adding a boundary.
- **Plain package names `contract`, `dispatcher`, `services`, `skills`, with `services.robot` and `services.vision`.** They match the folder names. A generic name such as `skills` or `services` could clash with another installed package; in this project's own virtual environment that is unlikely, and the short name reads naturally in imports and module strings.
- **A skill is one folder, `skills/<name>/`, holding its `SKILL.md` and its code; `skills.dir` is the folder of skill folders.** Adding a skill is adding a folder: the registry finds it by its `SKILL.md`, and the dispatcher reads what it needs to know about the skill (vision use, motion) from that skill's declarations, never from its name. The skill runtime shared by the skills is `skills/runtime/`. The `catalog` command and the catalog text the model sees are generated from the `SKILL.md` files ([skills.md](../skills/docs/skills.md#design-decisions)). Rejected: manifests in a separate `skills/catalog/<name>/` tree beside flat skill modules; a skill's description and its code then sit in two trees, and adding a skill touches both.
- **One `go2` command with subcommands** (`run`, `batch`, `catalog`, `state`, `bot`) and shared global options. Rejected: one console script per command (`go2-*`), which duplicates option handling and splits one tool into several names. The project and distribution name stays `go2-dispatcher`, matching the repository.

## Repository layout

```
go2-dispatcher/
├── pyproject.toml, uv.lock     # packaging; extras: robot (SDK), vision (YOLO)
├── config.example.toml         # copy to config.toml
├── .env.example                # ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN
├── contract/                   # contract package: what dispatcher, skills and services agree on; imports no project package
│   ├── docs/                   # contract.md
│   └── tests/                  # unit/: contract unit tests (excluded from the wheel)
├── dispatcher/                 # dispatcher package; never imports the SDK
│   ├── planning/               # plan model, submit_plan tool schema, validation, bounds, motion budget, verification
│   ├── llm/                    # planner protocol, prompt surface, retry loop, one module per provider
│   ├── transports/             # build_dispatcher, CLI, Telegram, transport texts
│   ├── docs/                   # loop-and-context.md, llm.md, run-log.md
│   └── tests/                  # unit/, helpers/, golden/: dispatcher unit tests (excluded from the wheel)
├── services/                   # the two services the dispatcher supervises
│   ├── backend/                # shared by both services: stub state file, DDS init
│   │   ├── docs/               # backend.md
│   │   └── tests/              # unit/: shared backend unit tests (excluded from the wheel)
│   ├── robot/                  # robot package: service, protocol and client, utilities, posture rule
│   │   ├── backends/           # real (SDK, DDS) and sim
│   │   ├── docs/               # service.md, robot.md
│   │   └── tests/              # unit/, robot/ (opt-in): robot unit tests (excluded from the wheel)
│   └── vision/                 # vision package: service, protocol and client, box classification
│       ├── detectors/          # real (camera, YOLO) and stub
│       ├── docs/               # vision.md
│       └── tests/              # unit/: vision unit tests (excluded from the wheel)
├── skills/                     # skills package: the loaded skill set (config skills.dir)
│   ├── runtime/                # skill-side runner, fault hook, motion helpers
│   ├── <name>/                 # one folder per skill: SKILL.md, skill.py, __init__.py, __main__.py
│   ├── docs/                   # skills.md
│   └── tests/                  # unit/: skills unit tests (excluded from the wheel)
├── tests/                      # cross-package tests: integration/, shared helpers/, pytest_plugin.py (options)
├── docs/                       # shared documentation (index: docs/README.md)
├── logs/                       # run logs by provider and backend (gitignored)
└── models/                     # YOLO weights (gitignored)
```

## Glossary

| Term | Meaning |
|---|---|
| Task | One operator message, carried out from receipt to a final outcome. |
| Plan | One `submit_plan` reply: `status`, `steps`, optional `replan_after`, `message`. |
| Step | One skill call inside a plan. |
| Skill | A robot capability the model can call, defined by a folder in `skills` holding its `SKILL.md` and code. |
| Utility | `stop_move` or `read_state`: run like a skill, never offered to the model. |
| LLM call | One request to the LLM that produced a response, valid or not. Infrastructure retries are not separate calls. |
| Return reason | Why the dispatcher calls the LLM: `initial`, `plan_complete`, `checkpoint`, `failure`, `schema_retry`. |
| Failure | A step outcome of `error`, `timeout`, `malformed`, `rejected` or `motion_budget_exceeded`. |
| Horizon | Maximum number of steps allowed in one plan (`loop.planning_horizon`). |
| Checkpoint | `replan_after = N`: the model asks to be called again after step N of its plan. |
| Posture | `standing`, `sitting` or `unknown`, derived from sampled robot state. |
| Stop path | Kill the running skill's process group and `stop` through the robot service; if it does not confirm within 500 ms, send `StopMove` from a fresh process. |
| Backend | `real` (Unitree SDK over DDS, in the robot service) or `stub` (`SimRobot` in the robot service, with fault injection). |
| Condition | Free-text experiment label (`run.condition`) copied into the run log. |
| Base dir | The folder relative config paths resolve against, and the working directory of every subprocess. |
