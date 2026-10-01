# Architecture

A condensed overview of the v1 design. The normative detail is in the spec: [`docs/spec/`](spec/) (§ numbers are global across its four parts).

## Purpose

An operator sends a natural-language task over Telegram or the CLI. An LLM returns a **plan**: an ordered list of skill calls, submitted through a single forced tool, `submit_plan`. The dispatcher validates the plan, runs each step as a separate subprocess that drives a Unitree Go2 EDU (or a stub), and feeds structured results back. The model then answers `DONE`, `PLAN` (more steps) or `ABORT`. Limits bound every task. A complete JSONL run log is written per task; it is the dataset for a later study of tokens, latency and replanning (§1).

Design choices behind this shape:
- The plan is a first-class object, so it can be logged, counted and inspected.
- Context is rebuilt from fixed slots on every call, not carried as a conversation, so measurements are comparable across experimental conditions.
- One process per skill call. The Unitree SDK initialises DDS through a process-wide singleton; a fresh process gets a clean channel and can be killed if it hangs.

## Components

```
 Operator ──► Transport (Telegram | CLI)
                │  run_task(text) / request_stop() / shutdown()
                ▼
           Dispatcher ─────────────────────► Run log (runs/*.jsonl, runs/index.jsonl)
            │    ▲
   context  │    │ plan
            ▼    │
       LLM client (Anthropic Messages API, forced tool submit_plan)
            │
            ▼
  Plan validation ─► bounds ─► motion budget
            │
            ▼
        Executor ── one subprocess per step ──► python -m go2_skills.<skill>
            ▲                                          │
            │   one JSON line (SkillResponse)          ▼
            └────────────────────────────── Backend: real SDK | stub
```

| Module (`src/go2_dispatcher/`) | Role |
|---|---|
| `config.py` | Config models, path resolution, `.env` parser (`docs/configuration.md`) |
| `registry.py` | Loads `skills/*/SKILL.md`, renders the catalog, computes the registry hash |
| `prompts.py`, `render.py`, `context.py` | All fixed text; step-line renderer; user-message assembly |
| `llm.py` | `AnthropicPlanner`, tool schema, infra retries |
| `validation.py`, `bounds.py`, `budget.py` | Plan-level checks; per-step bounds and precheck; motion budget |
| `executor.py` | Subprocesses, timeouts, kill, StopMove, read_state |
| `dispatcher.py` | The loop, busy lock, stop, shutdown |
| `runlog.py` | Per-task JSONL writer and index |
| `process_lock.py` | Machine-wide single-instance lock |
| `transports/` | `build_dispatcher`, `format_outcome`, CLI, Telegram |

| Module (`src/go2_skills/`) | Role |
|---|---|
| `policy_base.py` | `SkillPolicy`, `MotionCost` (no third-party imports) |
| `result.py` | stdout capture, `emit()`, `run_skill()`, orphan watchdog |
| `backend.py`, `real.py`, `stub.py`, `posture.py` | Backend switch, SDK wrappers (lazy imports), stub, posture rule |
| `walk.py` … `detect_object.py`, `stop_move.py`, `read_state.py` | Five skills and two utilities |

The dispatcher never touches DDS or the SDK; only skill and utility processes do. It imports skill modules only to read their `POLICY` objects, and importing a skill module never imports the SDK.

## The loop

Plan → validate → execute → feed back, until `DONE`, `ABORT` or a limit. In outline (§15.3; detail in `docs/loop-and-context.md`):

1. Check for stop and the deadline; check the call budget.
2. Build the context and call the LLM. If the reply is invalid, retry once with the errors appended.
3. `DONE` / `ABORT` end the task.
4. `PLAN`: precheck every step (bounds) and the motion budget. A rejection means nothing runs; it counts as a failure, and the model is called again.
5. Run steps up to `replan_after` (or all of them), one subprocess each. A failure abandons the rest of the plan.
6. Call the model again with the reason `plan_complete`, `checkpoint` or `failure`.

## Plan contract

`submit_plan` input: `status` (`PLAN` / `DONE` / `ABORT`), `steps` (`[{skill, params}]`, at most `planning_horizon`), optional `replan_after` (1-based checkpoint), `message` (required for DONE / ABORT). Tool use is non-strict; the dispatcher's own validation enforces the rules. Plans longer than the horizon are rejected, never truncated. No prompt caching, streaming or extended thinking (§12).

## Context slots

The tools (S6) and two system blocks (S1 rules, S2 catalog) are fixed for a given config. One user message holds Previous task (S7), Robot posture, Task (S3), Budget, Executed so far (S4, last K entries), Remaining plan (S5, `[pending]` / `[abandoned]`), and Notice. A schema retry appends a Rejection section. Nothing reaches the model that the dispatcher did not shape: no tracebacks, stderr or provider error text (§11, §18).

## Skills and backends

Each skill is a `SKILL.md` (frontmatter → catalog) plus a module with `main()` and `POLICY` (timeout formula, motion cost, which observations the model sees). Skills print exactly one JSON `SkillResponse` line, with robot state sampled at start and end. State is logged only; the model sees a derived posture. The backend is chosen by `GO2_BACKEND`. The stub replaces only the SDK layer, remembers sitting/standing in a state file, and supports fault injection (`error`, `hang`, `crash`, `garbage`). See `docs/skills.md`.

## Limits and the stop path

| Limit | Default | Outcome |
|---|---|---|
| Failure budget | 3 | `FAILURE_BUDGET_EXHAUSTED` |
| LLM-call budget | 20 | `CALL_BUDGET_EXHAUSTED` |
| Task time limit | 300 s | `TIME_LIMIT_EXCEEDED` |
| Per-step timeout | skill policy | `timeout` failure |
| Motion budget | 10 m / 720° commanded | `motion_budget_exceeded` failure |
| Horizon | 5 steps | invalid reply → retry |

The operator `stop`, step timeouts, the task time limit, shutdown and internal errors all use one stop path: kill the skill's process group, then send `StopMove` from a fresh process that a stop can never kill. Skills also stop themselves if the dispatcher dies. The remote e-stop remains the final safety measure. See `docs/safety.md`.

## Run log

One JSONL file per task plus `index.jsonl`. Records cover the task start (full config, prompts, registry hash, versions), every LLM request and response (`usage` verbatim, latency, retries), plans and rejections, every step (params, response, robot state, timing), stops and StopMoves, and the task end. See `docs/run-log.md`.

## v2 seams

Kept in v1 so that v2 can build on them (§23.1):

- `StepResult.verification` (always `"unverified"`), for v2 verification verdicts.
- `state_before` / `state_after` on every skill response and `state_after` on every StopMove, including the robot's own `position` estimate.
- `expect` accepted (and ignored) in SKILL.md frontmatter.
- Plan status normalised in one place (`Plan._normalise_status`), so adding `ASK` is local.
- The stub state file and stub `sample_state()`, so the stub can later simulate motion.
- `derive_posture()` as the single posture rule.
- `skills.dir` is configurable, so other skill sets (granularity tiers) can be loaded.

Planned for v2: verification (fault and goal verification, preconditions), the `ASK` status, and a stub that integrates commanded velocity. Further ideas: `docs/future-ideas.md`. Open questions: `docs/open-decisions.md`.
