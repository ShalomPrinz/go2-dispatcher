# Architecture

How the system is put together and why. Behaviour details live in the topic documents linked below; the code is the reference for everything it states unambiguously.

## Overview

An operator sends a natural-language task over Telegram or the CLI. The LLM returns a **plan**, an ordered list of skill calls, through a single tool, `submit_plan`. The dispatcher validates the plan, runs each step as a separate subprocess that drives the Go2 (or a stub), and feeds structured results back. The model then answers `DONE`, `PLAN` (more steps) or `ABORT`. Budgets and limits bound every task, and every task writes a JSONL run log.

## Components and data flow

```
 Operator ──► Transport (Telegram | CLI)
                │  run_task(text) / request_stop() / shutdown()
                ▼
           Dispatcher ─────────────────────► Run log (runs/*.jsonl, runs/index.jsonl)
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
            ▲                                          │
            │   one JSON line (skill response)         ▼
            └────────────────────────────── Backend: real SDK | stub
```

| Component | Code | Owner doc |
|---|---|---|
| Transports: CLI (`go2`) and Telegram (`go2 bot`), both over the same dispatcher | `dispatcher/transports/` | [running.md](running.md) |
| Dispatcher loop, busy-reject, stop, shutdown | `dispatcher.py` | [loop-and-context.md](loop-and-context.md), [safety.md](safety.md) |
| Context assembly and every fixed text | `context.py`, `prompts.py`, `render.py` | [loop-and-context.md](loop-and-context.md) |
| LLM client, tool schema, infra retries | `llm.py` | [llm.md](llm.md) |
| Plan validation, parameter bounds, motion budget | `validation.py`, `bounds.py`, `budget.py` | [loop-and-context.md](loop-and-context.md), [safety.md](safety.md) |
| Skill registry and generated catalog | `registry.py`, `skills/*/SKILL.md` | [skills.md](skills.md) |
| Executor: subprocesses, timeouts, kill, `StopMove`, state reads | `executor.py` | [safety.md](safety.md) |
| Skill runtime, skills and utilities, real and stub backends, posture rule | `skills/` | [skills.md](skills.md), [robot.md](robot.md) |
| Run log and index | `runlog.py` | [run-log.md](run-log.md) |
| Configuration and `.env` | `config.py` | [configuration.md](configuration.md) |
| Single-instance lock | `process_lock.py` | [safety.md](safety.md) |

One task, in outline: build the context from fixed slots → call the LLM → validate the plan and pre-check every step → run steps up to the model's checkpoint (or all of them), one subprocess each → call the LLM again with the reason (`plan_complete`, `checkpoint` or `failure`) → repeat until `DONE`, `ABORT`, or a budget or limit ends the task. The full loop is in [loop-and-context.md](loop-and-context.md).

## Process model

- **One dispatcher process per machine**, enforced by a file lock, so two processes can never drive one robot ([safety.md](safety.md)).
- Inside it, the transport thread receives messages and one task runs at a time. While a task runs, other messages get a busy reply (no queue); only the stop word gets through. The Telegram bot handles updates concurrently so that `stop` can arrive during a task.
- **One subprocess per skill call** (`python -m skills.<skill> '<json params>'`), in its own process group, never reused. It prints exactly one JSON response line and exits. Two utilities, `stop_move` and `read_state`, run the same way but are not skills.
- The robot is stationary while the LLM is called; motion happens only inside skill processes.
- The **dispatcher never imports the SDK or touches DDS**. It imports skill modules only to read their policy objects (timeout formula, motion cost, observations shown to the model), and importing a skill module never imports the SDK.
- Skill processes watch their parent and stop the robot if the dispatcher dies ([safety.md](safety.md)).

## Why a purpose-built dispatcher

The predecessor ran on OpenClaw, a general agent runtime. OpenClaw is good at connectivity: channels, sessions, plumbing. It was replaced as the runtime for three reasons.

1. **Measurement validity.** OpenClaw compacts context and retries on overflow at times that depend on session history. That contaminates exactly the dependent variables: tokens, latency and replanning. Its context grows with the session and includes content outside experimental control. (So far this is an argument, not data: analysing the old OpenClaw session logs is pending, see [roadmap.md](roadmap.md#pending-human-work).)
2. **No grounding layer to lose.** OpenClaw does not check an LLM's claims against reality, so verification had to be built either way.
   - Tool-loop detection (`tools.loopDetection`) is repetition-based and disabled by default.
   - The `tool_result_persist` hook only transforms results before they are stored.
   - The audit ledger holds metadata only (no arguments or results), so it cannot serve as an experiment log.
3. **The plan as an object.** Under a generic tool-use loop the plan is implicit in a sequence of calls and cannot be logged, counted or inspected.

What was lost, and how it is covered:

| Lost from OpenClaw | Coverage here |
|---|---|
| Provider retry | Own infrastructure retries with backoff, logged separately, never counted as LLM calls or replans ([llm.md](llm.md)) |
| Model failover | Not covered; accepted |
| Free-text replies to the operator | `message` field, required on `DONE` / `ABORT`, optional on `PLAN` ([loop-and-context.md](loop-and-context.md)) |
| Telegram integration | Own thin integration on `python-telegram-bot` ([running.md](running.md)) |
| Compaction | Removed deliberately: the context is fixed-slot and depends only on the current task |

OpenClaw remains in the project as a system-level baseline for the study ([project.md](project.md#openclaw-as-a-system-level-baseline)).

## Design decisions

Cross-cutting decisions. Decisions that belong to one topic are in that topic's document.

- **Measurement validity comes first.** Anything that would make tokens, latency or replanning depend on something other than the task and the experimental condition is avoided: no conversation history, no compaction, no prompt caching, thinking at its lowest setting, fixed notice texts identical across conditions. The individual decisions are in [loop-and-context.md](loop-and-context.md) and [llm.md](llm.md).
- **The plan is a first-class object, returned through one tool (`submit_plan`).** It can be validated, logged, counted and inspected, and every LLM call has one output contract. Rejected: a generic multi-tool loop, where the plan exists only implicitly.
- **Context is rebuilt from fixed slots on every call, not carried as a conversation.** Context size depends only on the current task, which keeps measurements comparable across conditions. Static content (tools, system text, catalog) comes first so prompt caching could be added later without reordering. Layout in [loop-and-context.md](loop-and-context.md).
- **One subprocess per skill call.** The Unitree SDK initialises DDS through a process-wide singleton, so a fresh process guarantees a clean channel. It also lets a hung call be killed, and isolates crashes in the native bindings.
- **One stop path** (kill the skill's process group, then send `StopMove` from a fresh process) for operator stop, step timeouts, the task time limit, shutdown and internal errors: one tested mechanism, and a killed process cannot clean up after itself ([safety.md](safety.md)).
- **Robot state is sampled at the start and end of every step and logged only.** v1 issues no verdicts; the model sees only a derived posture. This collects the data needed to set v2 verification thresholds before seeing any verification results ([roadmap.md](roadmap.md#v2-plan)).
- **The stub replaces only the SDK layer inside the skill process**, so process start, timeouts and kills are exercised for real in offline development and tests ([skills.md](skills.md)).
- **Runs offline by default.** Stub mode and the default test suite need no SDK, API key, network or robot; SDK, CycloneDDS and vision libraries are imported lazily, only on real-backend paths. `uv` and `pyproject.toml`, no `sys.path` changes ([setup.md](setup.md)).
- **Every starting value is a config key or a named constant**, never inline, because almost all of them are expected to be tuned ([configuration.md](configuration.md)).
- **Simple, inspectable mechanisms** over clever ones (for example fixed settle waits rather than motion detection, commanded rather than estimated motion budget), matching the project's scope and the student's background.

## Repository layout

```
go2-dispatcher/
├── pyproject.toml, uv.lock     # packaging; extras: robot (SDK, CycloneDDS), vision (YOLO)
├── config.example.toml         # copy to config.toml
├── .env.example                # ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN
├── dispatcher/                 # dispatcher package; never imports the SDK
│   ├── transports/             # build_dispatcher, CLI, Telegram
│   └── tests/                  # unit/, integration/, helpers/, golden/ (excluded from the wheel)
├── skills/                     # skills package: skill and utility processes, backends, posture rule
│   ├── catalog/<name>/SKILL.md # the loaded skill set (config skills.dir)
│   └── tests/                  # unit/, integration/, robot/ (opt-in) (excluded from the wheel)
├── conftest.py                 # pytest options: --run-live, --run-robot, --update-golden
├── docs/                       # this documentation
├── runs/                       # run logs (gitignored)
└── models/                     # YOLO weights (gitignored)
```

## Glossary

| Term | Meaning |
|---|---|
| Task | One operator message, carried out from receipt to a final outcome. |
| Plan | One `submit_plan` reply: `status`, `steps`, optional `replan_after`, `message`. |
| Step | One skill call inside a plan. |
| Skill | A robot capability the model can call, defined by a `SKILL.md` and a module in `skills`. |
| Utility | `stop_move` or `read_state`: run like a skill, never offered to the model. |
| LLM call | One request to the LLM that produced a response, valid or not. Infrastructure retries are not separate calls. |
| Return reason | Why the dispatcher calls the LLM: `initial`, `plan_complete`, `checkpoint`, `failure`, `schema_retry`. |
| Failure | A step outcome of `error`, `timeout`, `malformed`, `rejected` or `motion_budget_exceeded`. |
| Horizon | Maximum number of steps allowed in one plan (`loop.planning_horizon`). |
| Checkpoint | `replan_after = N`: the model asks to be called again after step N of its plan. |
| Posture | `standing`, `sitting` or `unknown`, derived from sampled robot state. |
| Stop path | Kill the running skill's process group, then send `StopMove` from a fresh process. |
| Backend | `real` (Unitree SDK over DDS) or `stub` (simulated, with fault injection). |
| Condition | Free-text experiment label (`run.condition`) copied into the run log. |
| Base dir | The folder relative config paths resolve against, and the working directory of every subprocess. |
