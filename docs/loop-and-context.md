# The loop and the context

How one task runs, what the model sees on each call, and how the budgets end a task. Spec: §11–§13, §15, §18.

## The loop

```
task text
  └─► [check stop / deadline] ─► [call budget left?] ─► build context ─► LLM call
          ▲                                                                   │
          │                                    invalid reply ─► one schema retry ─► still invalid → LLM_INVALID
          │                                                                   │
          │                                    DONE → DONE      ABORT → ABORTED
          │                                                                   │ PLAN
          │                                    precheck (bounds on every step, motion budget up to stop_at)
          │                                      rejected → failure +1 ───────┤
          │                                                                   ▼
          │                                    run steps 1..stop_at, one subprocess each
          │                                      step fails → failure +1, rest abandoned
          │                                      interrupted → STOPPED / TIME_LIMIT_EXCEEDED
          └────────── return reason: plan_complete | checkpoint | failure ◄───┘
```

1. Before every LLM call: if a stop was requested, the task ends `STOPPED`. If the task deadline has passed, it ends `TIME_LIMIT_EXCEEDED` (StopMove is sent in both cases). If `max_llm_calls` calls have been made, it ends `CALL_BUDGET_EXHAUSTED`.
2. The context is built from scratch (below) and sent. Interrupts are checked again right after the call, so a stop that arrives during a call never triggers a schema retry.
3. If the reply is invalid, there is exactly one schema retry (below).
4. `DONE` and `ABORT` end the task with the model's `message`.
5. `PLAN`: `stop_at = replan_after or len(steps)`. The whole plan is prechecked. If the precheck rejects the plan, nothing runs: one failure is counted and the model is called again with reason `failure`.
6. Steps `1..stop_at` run in order. Before each step, interrupts are checked. The step's motion cost is charged to the budget and the subprocess starts (`docs/skills.md`).
7. After the plan:
   - a step failed → reason `failure`; the steps after it are `[abandoned]`;
   - `stop_at < len(steps)` → reason `checkpoint`; the steps after `stop_at` are `[pending]`;
   - otherwise → reason `plan_complete`.

   (`replan_after == len(steps)` counts as `plan_complete`.)
8. The model is called again. A new plan always replaces the old one.

## Return reasons

| Reason | When | Notice shown |
|---|---|---|
| `initial` | first call of the task | `(none)` |
| `plan_complete` | the previous plan ran to the end | `Your previous plan ran to completion. Return DONE with a message if the task is complete; otherwise plan the next steps.` |
| `checkpoint` | the plan had `replan_after` < its length | `You asked to review results after step {n} of your previous plan. Its remaining steps are listed under "Remaining plan"; include them again if you still want them.` |
| `failure` | a step failed, or the plan was rejected | `Your previous plan failed at step {n} ({skill}): {outcome}. This is failure {f} of {max_failures}. Revise the plan to avoid that failure, or return ABORT with a message if the task cannot be done.` |
| `schema_retry` | the reply to the previous call was invalid | the original request's notice, unchanged, plus the Rejection section |

`{n}` is the step's position within its plan (1-based), not the task-wide step index.

## What counts

**A failure** is a step outcome of `error`, `timeout`, `malformed`, `rejected` (bounds) or `motion_budget_exceeded`. Bounds and motion-budget rejections count, following the professor's definition of failure (OD-11). `task_end.rejections` counts them separately. These are not failures: checkpoints, completed plans, invalid LLM replies, infra retries, and `interrupted` steps.

**An LLM call** is one request that got a response back, valid or not. A schema retry is an LLM call. Infra retries (connection errors, 408/409/429/5xx with backoff) are **not** separate calls. They appear as `llm_retry` records. A call that ends in `LLMUnavailable` or `LLMInterrupted` is not counted.

## Budgets and outcomes

| Limit | Config | Checked | Outcome |
|---|---|---|---|
| Failure budget | `loop.max_failures` | right after the failure that reaches it (no extra LLM call) | `FAILURE_BUDGET_EXHAUSTED` |
| Call budget | `loop.max_llm_calls` | before every call, including the schema retry | `CALL_BUDGET_EXHAUSTED` |
| Task time limit | `loop.task_time_limit_s` | before each call and step, during LLM retries, and by the executor while a step runs | `TIME_LIMIT_EXCEEDED` |
| Horizon | `loop.planning_horizon` | on every reply, before schema validation | invalid reply → schema retry |
| Motion budget | `motion_budget.*` | precheck, over steps `1..stop_at`, starting from the task's current usage | `motion_budget_exceeded` failure |
| Step timeout | the skill's `POLICY.timeout_s(params)` | by the executor | `timeout` failure |

All task outcomes:

| Outcome | When | Operator message |
|---|---|---|
| `DONE` | the model returned `DONE` | the model's message |
| `ABORTED` | the model returned `ABORT` | the model's message |
| `STOPPED` | operator `stop` or shutdown | `Stopped on request. A stop command was sent to the robot.` |
| `TIME_LIMIT_EXCEEDED` | time limit reached | `Stopped: the task exceeded its {limit}s time limit. A stop command was sent to the robot.` |
| `FAILURE_BUDGET_EXHAUSTED` | failures reached `max_failures` | `Stopped: the robot failed {n} times while trying this task. Last failure: {skill}: {error_message}` |
| `CALL_BUDGET_EXHAUSTED` | `max_llm_calls` reached | `Stopped: the task reached the limit of {n} planning calls without finishing.` |
| `LLM_INVALID` | a reply and its retry were both invalid | `Stopped: the model returned an invalid plan twice.` |
| `LLM_ERROR` | LLM unreachable after infra retries, or a non-retryable API error | `Stopped: the model could not be reached ({detail}).` |
| `INTERNAL_ERROR` | unhandled exception in the dispatcher | `Stopped: internal error ({ExceptionType}). See run log {run_id}.` |

If a StopMove failed during the task, the message ends with ` WARNING: the stop command to the robot failed. Stop the robot manually.`

## Plan contract

The model must call the single tool `submit_plan` (`tool_choice` is `auto`, so the model is asked rather than forced to call it; non-strict tool use; no `temperature`; `thinking` from `llm.thinking`, off by default):

```json
{"status": "PLAN", "steps": [{"skill": "turn", "params": {"direction": "left", "angle_deg": 90}}], "replan_after": 1, "message": "optional"}
```

Validation (§13.1), in this order:

1. **Horizon** (on the raw input): more than `planning_horizon` steps → `plan has {n} steps; the maximum is {horizon}`. The plan is **never truncated**. A `horizon_rejection` record is logged.
2. **Schema**: `status` ∈ PLAN/DONE/ABORT (case-insensitive), `replan_after` a strict integer, no extra keys.
3. **Semantics**: PLAN needs ≥ 1 step; DONE/ABORT need no steps and a non-blank message; `replan_after` only with PLAN and within `1..len(steps)`.

Other invalid replies: no `submit_plan` call (`no submit_plan call in reply`; for example a text-only reply), and `stop_reason = max_tokens` (`reply was cut off; keep the plan shorter`). `thinking` and `text` blocks before the `submit_plan` call are skipped; all blocks are logged in `llm_response.content`.

Step bounds (§13.2), checked on every step of a PLAN before anything runs: the skill exists; no undeclared params; required params are present; types are right (enum values are trimmed and lowercased; an integral float is accepted for `integer`); ranges are respected; defaults are filled in. The first step with violations rejects the whole plan. The rejected step is recorded with its raw params, `error_code = "bounds"`, and all its violations joined with `; ` (cut to 200 chars).

## Context layout

Every request is built from fixed slots. No conversation is carried over, and each request has exactly one user message. Context size depends only on the current task.

| Order | Slot | Where | Changes |
|---|---|---|---|
| 1 | Output contract (S6) | `tools`: the `submit_plan` schema | never, for a given config |
| 2 | System role and rules (S1) | `system[0]` | never, for a given config |
| 3 | Skill catalog (S2) | `system[1]` = `"## Skills\n" + catalog` | never, for a given config |
| 4 | Previous task (S7) | user message | between tasks |
| 5 | Robot | user message | after steps |
| 6 | Task (S3) | user message | never within a task |
| 7 | Budget | user message | after steps and calls |
| 8 | Executed so far (S4) | user message | after steps |
| 9 | Remaining plan (S5) | user message | after plans |
| 10 | Notice | user message | per call |
| 11 | Rejection | user message | schema retry only |

Static content comes first (tools, then system), so prompt caching can be added later without reordering. Caching is not enabled in v1. `go2-dispatch catalog` prints slots 1–3 exactly.

The user message always has all seven sections, in this order, separated by one blank line. An empty section shows a placeholder (`(none)` or `(nothing yet)`), so the shape never changes.

### Full example: the request after a failure

Task: "Turn left, walk two metres and look for a chair". The first plan was `turn left 90`, `walk forward 2`, `detect_object chair`. The walk failed (injected stub fault `error` at step 2). This is call 2, reason `failure`:

```
## Previous task
(none)

## Robot
Posture: standing

## Task
Turn left, walk two metres and look for a chair

## Budget
Travel: 2 of 10 m used
Rotation: 90 of 720 deg used
Failures: 1 of 3
Model calls: 1 of 20

## Executed so far
1. turn(direction=left, angle_deg=90) -> ok
2. walk(direction=forward, distance_m=2) -> error: Move returned 99

## Remaining plan
3. detect_object(target=chair) [abandoned]

## Notice
Your previous plan failed at step 2 (walk): error. This is failure 1 of 3. Revise the plan to avoid that failure, or return ABORT with a message if the task cannot be done.
```

The failed walk is still charged 2 m of travel: the budget is charged when a step is dispatched, because commanded motion may have happened.

### Sections

- **Previous task:** the last finished task in this process (`Task`, `Outcome`, `Message`, `Last step` without its number). After a restart it is `(none)`.
- **Robot:** `Posture: standing | sitting | unknown`. After every dispatched step, the posture is taken from the step's `state_after`. If there is none, it comes from the StopMove's `state_after`. If there is none and the step was killed, it becomes `unknown`. Otherwise it is unchanged. The posture carries over to the next task.
- **Task:** the operator's text, trimmed, never rewritten.
- **Budget:** numbers rounded to 2 decimals and printed with `:g`. `Model calls` counts the calls completed **before** this request.
- **Executed so far:** one line per recorded step:
  ```
  {index}. {skill}({params}) -> ok{: k=v, ... for context_observations}
  {index}. {skill}({params}) -> {outcome}: {error_message}
  - rejected before running: {skill}({params}) -> {outcome}: {error_message}
  ```
  Params are written as `name=value` in frontmatter order, strings without quotes, numbers with `:g`. Error messages are one line of at most 200 characters (cut to 199 + `…`). Stderr, tracebacks and raw stdout never appear. Rejected steps use the `-` form, because they did not run.
- **Remaining plan:** the steps of the latest plan that did not run, with their position in the plan: `[pending]` after a checkpoint, `[abandoned]` after a failure. After a rejection, **every** step of the plan is listed as `[abandoned]`, including the rejected one. These steps do not run unless the model includes them again.
- **Notice:** see the return reasons above.

### Truncation

If more than `context_history_k` (K, default 10) steps have been recorded in the task, the section starts with `({n} earlier entries omitted)` and then shows the last K entries. Nothing is summarised. The operator's outcome text (`docs/running.md`) is not cut to K.

### Schema retry

If a reply is invalid, the dispatcher makes exactly **one** retry. The system blocks and tools are the same. The user message is the original user message, byte for byte, plus `"\n\n"` and:

```
## Your previous reply was rejected
{one line per validation error}
Call submit_plan again with a corrected plan.
```

The retry has reason `schema_retry` (`retry_of` in the log holds the reason being retried) and counts as an LLM call. If the retry is also invalid, the task ends `LLM_INVALID`. Invalid replies never count as failures.

## Horizon and call budget

`planning_horizon` and `max_llm_calls` interact (OD-13). With horizon 1, every step needs its own call, plus a final call for `DONE`. A task that needs 10 steps uses at least 11 calls at horizon 1, but can need as few as 3 at horizon 5. With the default `max_llm_calls = 20`, long tasks at a small horizon can end `CALL_BUDGET_EXHAUSTED`, so in that condition the rate of that outcome partly reflects the cap. Both values are logged with every task (`task_start.config`, `index.jsonl`). Set `max_llm_calls` per condition when designing an experiment.

The horizon is stated in the system text and enforced by the tool schema (`maxItems`) and by validation. A plan that is too long is rejected and retried, not truncated. Watch the `horizon_rejection` rate in the run log (see `docs/future-ideas.md`).
