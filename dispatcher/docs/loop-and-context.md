# The loop and the context

How one task runs, what the model must return, how a plan is checked before it runs, what the model sees on each call, and how budgets end a task. The LLM request itself (model, parameters, retries) is in [llm.md](llm.md); the stop path and the meaning of the motion budget are in [safety.md](../../docs/safety.md).

## The loop

```
task text
  └─► [stop / deadline?] ─► [call budget left?] ─► build context ─► LLM call
          ▲                                                             │
          │                         invalid reply ─► one schema retry ─► still invalid → LLM_INVALID
          │                                                             │
          │                         DONE → DONE        ABORT → ABORTED  │
          │                                                             │ PLAN
          │                         pre-check (bounds on every step, motion budget up to stop_at)
          │                           rejected → failure +1 ────────────┤
          │                                                             ▼
          │                         run steps 1..stop_at, one subprocess each
          │                           step fails → failure +1, rest abandoned
          │                           interrupted → STOPPED / TIME_LIMIT_EXCEEDED
          │                           robot service lost → fallback stop → ROBOT_SERVICE_LOST
          │                           stop failed, service alive → STOP_FAILED
          └── return reason: plan_complete | checkpoint | failure                      ◄┘
```

1. Before every LLM call: a requested stop ends the task `STOPPED`; a passed deadline ends it `TIME_LIMIT_EXCEEDED` (both send `StopMove`, see [safety.md](../../docs/safety.md)). If `loop.max_llm_calls` calls have been made, the task ends `CALL_BUDGET_EXHAUSTED`.
2. The context is built from scratch ([below](#context-layout)) and sent. Stop and deadline are checked again right after the reply, so a stop that arrives during a call never leads to a schema retry.
3. An invalid reply gets exactly one schema retry ([llm.md](llm.md#schema-retry)).
4. `DONE` and `ABORT` end the task with the model's `message` (`DONE` without one gets `prompts.DONE_DEFAULT_MESSAGE`).
5. `PLAN`: `stop_at = replan_after or len(steps)`. The whole plan is pre-checked ([below](#whole-plan-pre-check)). A rejected plan runs nothing: one failure is counted and the model is called again with reason `failure`.
6. Steps `1..stop_at` run in order. Before each step, stop and deadline are checked; the step's motion cost is charged; the skill subprocess starts ([skills.md](../../skills/docs/skills.md)). Before a `detect_object` step the executor first makes sure the vision service runs and waits for it to be ready; that wait counts against the task time limit, not the step timeout ([running.md](../../docs/running.md#vision-service)). A robot service lost during a step ends the task `ROBOT_SERVICE_LOST` with no further call ([below](#robot-service-lost)). A killed step whose stop failed with the service alive ends it `STOP_FAILED`, also with no further call ([below](#stop-failed)).
7. After the plan:
   - a step failed → reason `failure`; the steps after it are listed as `[abandoned]`;
   - `stop_at < len(steps)` → reason `checkpoint`; the steps after `stop_at` are listed as `[pending]`;
   - otherwise → reason `plan_complete` (`replan_after == len(steps)` counts as complete).
8. The model is called again. A new plan always replaces the old one; leftover steps run only if the model includes them again.

Every task ends in one place. Inside the loop, an ending (outcome code plus the operator-message inputs and any `StopMove` result) is raised as `_TaskEnded` carrying an `_Ending`, always after any `StopMove` was sent; `run_task` catches it and calls `_end` once, which writes `task_end` and the index row. Any other exception ends the task `INTERNAL_ERROR` after a `StopMove`; this is also the fallback for a `_TaskEnded` raised outside the loop.

## Plan contract

The model answers every call through the single tool `submit_plan`. Its input is the plan:

```json
{"status": "PLAN", "steps": [{"skill": "turn", "params": {"direction": "left", "angle_deg": 90}}], "replan_after": 1, "message": "optional"}
```

| Field | Meaning |
|---|---|
| `status` | `PLAN` (run the steps), `DONE` (task complete), `ABORT` (cannot or should not be done, or ambiguous). Matched case-insensitively after trimming. |
| `steps` | Ordered skill calls `{skill, params}`. At least one for `PLAN`, none for `DONE` / `ABORT`. At most `loop.planning_horizon`. |
| `replan_after` | Optional, `PLAN` only, `1..len(steps)`: run up to this step, then call the model again. |
| `message` | Required (non-blank) for `ABORT`: the text the operator receives. Asked for but optional for `DONE`: missing or blank, the operator gets `prompts.DONE_DEFAULT_MESSAGE`. Optional for `PLAN`: logged, and shown in the CLI trace and the Telegram progress message ([running.md](../../docs/running.md#progress-message)). |

The tool definition is generated by `llm.plan_tool_schema(horizon)`, with `maxItems` = the horizon. It and the system blocks are built once per process by `context.PromptSurface.build(registry, horizon)`, which the planner is built with and sends, the run log copies and `go2 catalog` prints for the current config. The request is sent non-strict, so the dispatcher's own validation enforces it.

## Plan validation

The loop checks every reply with `llm.check_reply`: the two reply checks below, then `llm.validate_tool_input` on the raw tool input, in this order. Every error becomes one line in the schema retry's Rejection section.

1. **Horizon**, on the raw input: more than `planning_horizon` steps → `plan has {n} steps; the maximum is {horizon}`. The plan is rejected, never truncated, and it is logged as `plan_invalid` with `rejection_kind = "horizon"` ([llm.md](llm.md#horizon-rejection)). Schema and semantic checks still run and add their errors.
2. **Schema** (pydantic `Plan`): unknown keys, wrong types (`replan_after` must be a strict integer, `message` a string). Each error is `{dotted location}: {message}` (`input: ...` when the whole input is wrong).
3. **Semantics**, only if the schema passed:
   - `status PLAN needs at least one step`
   - `status {s} must have no steps` (DONE / ABORT)
   - `status {s} needs a message` (ABORT only, missing or blank)
   - `replan_after is only allowed with status PLAN`
   - `replan_after must be between 1 and {len(steps)}`

A plan is accepted only with no errors at all. Two more invalid replies are detected before validation ([llm.md](llm.md#response-handling)): a reply cut off at `max_tokens` (`reply hit the output token limit before submit_plan was complete; reason more briefly and submit the plan sooner`) and a reply without a `submit_plan` call (`no submit_plan call in reply`).

## Step bounds

`bounds.check_step` checks one step against its skill's `SKILL.md` declaration ([skills.md](../../skills/docs/skills.md#skillmd-frontmatter)) and collects every violation:

1. The skill exists, else `unknown skill '{s}'; available: {sorted names}` (nothing else is checked).
2. No undeclared params: `unknown parameter '{p}' for skill {s}`.
3. Required params present: `missing parameter '{p}' for skill {s}`.
4. Types, with no other coercion: `number` is an int or float, not bool, finite; `string` is non-empty after trimming (the trimmed value is kept); `enum` is a string whose trimmed, lower-cased value is in `values` (the normalised value is kept). A number too large to convert to float is a type violation. Message: `parameter '{p}' for skill {s} must be {expected}, got {repr}`, with `{expected}` one of `a finite number`, `a non-empty string`, `one of a, b, c`, and the repr cut to 40 characters.
5. Ranges (only for params whose type passed): `parameter '{p}' for skill {s} is {v:g}, outside {min:g} to {max:g}` (or `below the minimum {min:g}` / `above the maximum {max:g}` when one side is open).
6. Defaults are filled in for absent optional params, in frontmatter order.

The filled, normalised params are what is dispatched, logged and shown in the context. Skills do no param checks of their own: these step bounds and the whole-plan pre-check are the only param validation, and the dispatcher's parse of the response line against `SkillResponse` (defined in `skills/schema.py`, re-exported by `dispatcher/models.py`) is the only full check on a skill's response shape ([skills.md](../../skills/docs/skills.md)).

## Whole-plan pre-check

`bounds.precheck` runs on every `PLAN` before any step starts:

1. `check_step` on **every** step of the plan, including steps after `stop_at`. The first step with violations rejects the plan: a step result with `outcome = "rejected"`, `error_code = "bounds"`, the raw params, and all its violations joined with `; ` (cut to 200 characters).
2. If all steps pass, the motion budget is simulated over steps `1..stop_at` only, in order, on a copy of the task's budget starting from its current usage. The first step that would go over a limit rejects the plan with `outcome = "motion_budget_exceeded"` and the message `this step needs {need:g} {unit} of {kind} but only {left:g} {unit} remain for this task` (`kind` is `travel` in `m` or `rotation` in `deg`, travel checked first; values rounded to 2 decimals).

A rejection is recorded as one step result with no dispatch index, counts as one failure, and leads to a `failure` call. After a rejection, **every** step of the plan is listed as `[abandoned]` under Remaining plan, including the rejected one. Rejected steps are never charged to the motion budget. What the budget means and why it exists: [safety.md](../../docs/safety.md#motion-budget).

## Step outcomes

Every recorded step has one outcome:

| Outcome | `error_code` | Source | Failure? |
|---|---|---|---|
| `ok` | — | skill response `status = ok` | no |
| `error` | the skill's code ([skills.md](../../skills/docs/skills.md#error-codes)) | skill response `status = error` | yes |
| `error` | `robot_service_lost` | the robot service was lost: the step was killed with it (message `stopped: the robot control service was lost`), or could not be armed and never started | yes |
| `timeout` | `timeout` | killed after `POLICY.timeout_s(params)`; message `killed after {timeout_s:g}s timeout` | yes |
| `malformed` | `malformed` | no valid response line; message `skill process exited with code {rc} without a valid response` | yes |
| `rejected` | `bounds` | pre-check, before anything ran | yes |
| `motion_budget_exceeded` | `motion_budget_exceeded` | pre-check, before anything ran | yes |
| `interrupted` | `stopped_by_operator` / `task_time_limit` / `shutdown` | killed by a stop, the task time limit or shutdown; ends the task | no |

## What counts

**A failure** is a step outcome of `error`, `timeout`, `malformed`, `rejected` or `motion_budget_exceeded`. Checkpoints, completed plans, invalid LLM replies, infrastructure retries and `interrupted` steps are not failures. `task_end.rejections` counts the two pre-check outcomes separately, so they can be told apart in analysis.

**An LLM call** is one request that got a response back, valid or not. A schema retry is an LLM call. Infrastructure retries are not separate calls; a call that ends without a response (LLM unreachable, or interrupted while retrying) is not counted ([llm.md](llm.md#infrastructure-retries)).

## Return reasons

The reason for each call is logged on every `llm_request` record and selects the Notice the model sees.

| Reason | When |
|---|---|
| `initial` | first call of the task |
| `plan_complete` | the previous plan ran to the end |
| `checkpoint` | the plan had `replan_after` < its length |
| `failure` | a step failed, or the plan was rejected |
| `schema_retry` | the previous reply was invalid; the original request's notice is kept, plus the Rejection section |

The loop carries the reason together with the Remaining plan and the notice values in one frozen `context.Feedback`, built by one constructor per reason (`plan_complete`, `checkpoint`, `failure`). The notice texts are in `prompts.py` (exact text in `dispatcher/tests/golden/fixed_texts.txt`). In the `failure` notice, `{n}` is the step's position in its plan (1-based), not the task-wide index.

### Robot service lost

The executor reports a step during which the [robot service](../../docs/running.md#robot-service) was lost (`ExecResult.service_lost`) after it has SIGKILLed the service and the skill process group and run the fallback `stop_move` ([safety.md](../../docs/safety.md#robot-service-lost)). This includes a step that could not be armed and never started. The step is recorded with its outcome (a failure, usually `error`), then the stop. The dispatcher then ends the task `ROBOT_SERVICE_LOST`, with no further LLM call. The operator message says whether the fallback stop was confirmed.

The service stays down until the next task's [fresh state read](../../docs/running.md#robot-service) starts it; a failed start ends that next task `STATE_UNAVAILABLE`. A step never starts the service (`Executor.run` only arms it), so one task uses one service process, from the read to `task_end`.

A loss during a step that a stop, the time limit or shutdown interrupted ends the task with that outcome instead; only the stop path changes. A stop that arrives after the loss was detected does not change the outcome: the fallback stop already covers it, and the `stop_requested` record shows it.

### Stop failed

Within a task there is no LLM call without a confirmed stop. When a step comes back with a `stop_move` that is not `ok` and the service was not lost, the step and the stop are recorded and the dispatcher ends the task `STOP_FAILED` ([safety.md](../../docs/safety.md#stopmove-failure)). The step still counts as a failure, but this ending comes before the failure budget check. An `interrupted` step keeps its outcome (`STOPPED`, `TIME_LIMIT_EXCEEDED`) and a lost service keeps `ROBOT_SERVICE_LOST`; both already end the task, and their message ends with `prompts.STOP_MOVE_WARNING`. The next task is not gated; the warning is the safeguard ([safety.md](../../docs/safety.md#stopmove-failure)).

## Budgets and limits

| Limit | Config | Checked | Effect |
|---|---|---|---|
| Failure budget | `loop.max_failures` | right after the failure that reaches it (no further LLM call) | `FAILURE_BUDGET_EXHAUSTED` |
| Call budget | `loop.max_llm_calls` | before every call, including a schema retry | `CALL_BUDGET_EXHAUSTED` |
| Task time limit | `loop.task_time_limit_s` | before each call and step, during LLM retry backoff, and by the executor while a step runs | `TIME_LIMIT_EXCEEDED` |
| Horizon | `loop.planning_horizon` | on every reply | invalid reply → schema retry |
| Motion budget | `motion_budget.*` | pre-check, over steps `1..stop_at` | `motion_budget_exceeded` failure |
| Step timeout | the skill's `POLICY.timeout_s(params)` | by the executor | `timeout` failure |

All values are starting values and *tunable* ([configuration.md](../../docs/configuration.md)).

### Horizon and call budget

`planning_horizon` and `max_llm_calls` interact. At horizon 1 every step needs its own call, plus a final call for `DONE`: a 10-step task needs at least 11 calls at horizon 1 but can need 3 at horizon 5. With one `max_llm_calls` for all conditions, long tasks at a small horizon can end `CALL_BUDGET_EXHAUSTED`, so that outcome's rate partly reflects the cap. Both values are logged with every task (`task_start.config`, `index.jsonl`). Choosing `max_llm_calls` per condition is open ([roadmap.md](../../docs/roadmap.md#open-questions)).

## Task outcomes

| Outcome | When |
|---|---|
| `DONE` | the model returned `DONE`; the operator gets the model's `message`, or `prompts.DONE_DEFAULT_MESSAGE` if it is missing or blank |
| `ABORTED` | the model returned `ABORT`; the operator gets the model's `message` |
| `STOPPED` | operator stop or shutdown |
| `TIME_LIMIT_EXCEEDED` | task time limit reached |
| `FAILURE_BUDGET_EXHAUSTED` | failures reached `max_failures` |
| `CALL_BUDGET_EXHAUSTED` | `max_llm_calls` reached |
| `LLM_INVALID` | a reply and its schema retry were both invalid |
| `LLM_ERROR` | LLM unreachable after infrastructure retries, or a non-retryable API error |
| `STATE_UNAVAILABLE` | the task-start state read through the robot service failed; no LLM call was made |
| `ROBOT_SERVICE_LOST` | the robot service was lost during a step ([above](#robot-service-lost)) |
| `STOP_FAILED` | the stop of a killed step failed with the service alive; no further LLM call was made in this task ([above](#stop-failed)) |
| `INTERNAL_ERROR` | unhandled exception in the dispatcher |

`models.StopCause` maps each interrupt cause to its outcome (`operator` and `shutdown` → `STOPPED`, `task_time_limit` → `TIME_LIMIT_EXCEEDED`) and to what a killed step reports; its values are the logged `reason` and `cause` strings.

The other outcomes get a fixed operator message from `prompts.operator_message`, filled from `prompts.OutcomeFacts` (exact texts in `dispatcher/tests/golden/fixed_texts.txt`). `LLM_ERROR` names the error class and HTTP status (for example `RateLimitError 429`); `STATE_UNAVAILABLE` names why the read failed; `ROBOT_SERVICE_LOST` says whether the fallback stop was confirmed (`prompts.SERVICE_LOST_*`); `STOP_FAILED` says the robot may still be moving and relies on the warning below to tell the operator to stop it; `FAILURE_BUDGET_EXHAUSTED` names the last failure's skill and message (its outcome if it has none); `INTERNAL_ERROR` names the exception type and run id. If any `StopMove` failed during the task, the message ends with `prompts.STOP_MOVE_WARNING`. How transports show them is in [running.md](../../docs/running.md).

## Context layout

Every request is built from fixed slots. No conversation is carried over; each request has exactly one user message.

| Order | Slot | Where | Changes |
|---|---|---|---|
| 1 | Output contract | `tools`: the `submit_plan` definition | never, for a given config |
| 2 | System text | `system[0]` | never, for a given config |
| 3 | Skill catalog | `system[1]` = `"## Skills\n" + catalog` | never, for a given config |
| 4 | Previous task | user message | between tasks |
| 5 | Robot | user message | after steps |
| 6 | Task | user message | never within a task |
| 7 | Budget | user message | after steps and calls |
| 8 | Executed so far | user message | after steps |
| 9 | Remaining plan | user message | after plans |
| 10 | Notice | user message | per call |
| 11 | Rejection | user message | schema retry only |

Slots 1–3 come from `context.PromptSurface` (built once per process); `go2 catalog` prints them exactly. The catalog is generated from the registry ([skills.md](../../skills/docs/skills.md#catalog-and-registry-hash)).

`registry._render_skill` renders each skill the same way: a `name: description` line, one `  - param (...)` line per parameter (or `  - no parameters`), then one `  Heading: text` line per `SKILL.md` body section in the parsed order ([skills.md](../../skills/docs/skills.md#skillmd-body)). A multi-line section text keeps its line breaks, with each continuation line indented four spaces. The dispatcher iterates the parsed sections and never names the headings. The current catalog is `dispatcher/tests/golden/catalog.txt`.

### System text

`prompts.SYSTEM_TEMPLATE`, with `{horizon}` = `loop.planning_horizon`. It explains the loop (independent calls, a new plan replaces the old one) and the plan rules, including that a message which is not a robot task (a greeting, small talk, a general question) ends `DONE` with no steps and a one-sentence reply that says what the robot can do, and how to use the obstacle distances in the Robot section: they are a snapshot that a walk or turn makes stale (replan with `replan_after` to get new ones), the nearest sensor return rather than proof of a clear path, blind to glass, thin and low objects, silent on what the object is, and not a safety stop; a walk may not go farther than the distance in its direction minus 0.3 m (*tunable*), and a task that needs more gets a shorter walk with a reason in `message`, or `ABORT`; exact text in `dispatcher/tests/golden/fixed_texts.txt`, or run `go2 catalog`.

### User message

Always all seven sections, in this order, separated by one blank line, no trailing newline. An empty section shows a placeholder (`(none)` or `(nothing yet)`), so the shape never changes.

```
## Previous task
## Robot
## Task
## Budget
## Executed so far
## Remaining plan
## Notice
```

- **Previous task:** the last finished task in this process, or `(none)` (also after a restart):
  ```
  Task: {task}
  Outcome: {outcome code}
  Message: {operator message}
  Last step: {last dispatched step, rendered without its number, or (none)}
  ```
- **Robot:** two lines from the task's last known robot state:
  ```
  Posture: standing | sitting | unknown
  Obstacles (m): front {d}, back {d}, left {d}, right {d}
  ```
  `{d}` is `RobotState.obstacle_m` for that direction, rounded to 2 decimals and printed with `:g`, or `no reading` when that direction is null. When the state is unknown or its `obstacle_m` is null, the second line is `Obstacles (m): unavailable`, and with no state the posture is `unknown`. `range_obstacle_raw` and the other state fields never appear. The dispatcher keeps the last known `RobotState` per task. Before the task's first planning call it reads a fresh state through the robot service ([running.md](../../docs/running.md#robot-service)); if that read fails (the service cannot be started or reached, has had no message, or reports the link down), the dispatcher sends `StopMove` (reason `state_unavailable`) and the task ends `STATE_UNAVAILABLE` before any planning call, with the reason in `task_start.state_error` ([run-log.md](run-log.md#task_start)). After every dispatched step it takes the step's `state_after`; if there is none, the following stop's `state_after`; if there is none and the step was killed (`timeout`, `interrupted`), the state becomes unknown; otherwise it is unchanged. A dispatcher `StopMove` at task end also updates it. No state carries over between tasks. A stop during the task-start read is honoured: the task is already running for `request_stop`, so the operator gets the normal stop reply, and the task ends before any planning call: `STOPPED` after a successful read, `STATE_UNAVAILABLE` after a failed one, each after a `StopMove`. So the first plan of a task sees the robot as it is at task start, and every replanning round sees the latest `state_after`. How posture and obstacle distances are derived from state: [robot.md](../../skills/docs/robot.md).
- **Task:** the operator's text, trimmed, never rewritten.
- **Budget:**
  ```
  Travel: {used} of {max} m used
  Rotation: {used} of {max} deg used
  Failures: {failures} of {max_failures}
  Model calls: {calls made before this request} of {max_llm_calls}
  ```
  Numbers are rounded to 2 decimals and printed with `:g`.
- **Executed so far:** one line per recorded step, or `(nothing yet)`:
  ```
  {index}. {skill}({params}) -> ok{: k=v, ... for the skill's context_observations}
  {index}. {skill}({params}) -> {outcome}: {error_message}
  - rejected before running: {skill}({params}) -> {outcome}: {error_message}
  ```
  Params are `name=value` in frontmatter order (undeclared ones after, in received order), strings unquoted, numbers with `:g`, booleans `true` / `false`. Error messages are one line of at most 200 characters (cut to 199 + `…`). Stderr, tracebacks and raw stdout never appear. Rejected steps use the `-` form because they did not run.
- **Remaining plan:** steps of the latest plan that did not run, as `{position in plan}. {skill}({raw params}) [pending]` after a checkpoint or `[abandoned]` after a failure or rejection; otherwise `(none)`.
- **Notice:** see [return reasons](#return-reasons).

**History cut.** If more than `loop.context_history_k` (K, default 10, *tunable*) steps have been recorded, Executed so far starts with `({n} earlier entries omitted)` and shows the last K. Nothing is summarised. The operator's outcome text is cut separately ([running.md](../../docs/running.md#outcome-text)).

**Rejection section** (schema retry only): the user message rebuilt for the retry (the original one with the model-call count raised by the rejected call), plus `"\n\n"`, `prompts.REJECTION_HEADER`, one line per validation error, and `prompts.REJECTION_FOOTER`.

A failed step is still charged to the motion budget: the budget is charged at dispatch ([safety.md](../../docs/safety.md#motion-budget)). Exact user messages for each case are the golden files `dispatcher/tests/golden/context_*.txt` ([testing.md](../../tests/docs/testing.md)).

## Design decisions

- **The plan is returned through one tool, `submit_plan`, whose input is the plan object.** The plan is first-class: it can be validated, logged, counted and inspected, and every call has one output contract. Rejected: a generic tool-use loop, where the plan exists only implicitly in a sequence of calls ([architecture.md](../../docs/architecture.md#why-a-purpose-built-dispatcher)).
- **An up-front plan with model-chosen checkpoints.** The advisor's complaint about the inherited system was that the LLM guided the robot step by step, which was slow; he asked for a plan with explicit stop-and-report points. `replan_after` is the checkpoint the model chooses inside a plan; the horizon is a cap the dispatcher imposes. They are different things: without `replan_after` a plan is all-or-nothing. Horizon values 1 / k / large give one-step / batch / full-plan behaviour on one code path ([project.md](../../docs/project.md)).
- **Plans longer than the horizon are rejected, never truncated.** Truncation would silently run a plan the model never made and corrupt the logged data. The rejection rate is monitored ([llm.md](llm.md#horizon-rejection)).
- **The whole plan is pre-checked before any step runs** (bounds on all steps, motion budget on the steps to run). A plan either runs within limits or does not start; no partial motion happens because of a later bad step.
- **Failures are skill error, timeout, malformed output, bounds rejection and motion-budget rejection.** This follows the advisor's definition of failure (skill error, bounds rejection, timeout); motion-budget rejections are treated like bounds rejections. Checkpoints and completed plans never count. The failure budget counts failures only.
- **A separate cap on LLM calls per task** stops endless *successful* loops, which the failure budget cannot catch. The return reason is logged on every call; without it, horizon 1 would look like constant replanning.
- **A message that is not a robot task ends `DONE` with no steps and a one-sentence reply that says what the robot can do.** One fixed line in the system text makes this part of the contract instead of leaving it to the model. In run-log analysis, `DONE` with 0 steps is only a candidate filter for these messages: a real task that is already satisfied also ends that way, so check the task text too. Rejected: `ABORT`, which would count chit-chat as a failed task; a keyword filter in the bot, which is fragile and would split the decision between transport and planner.
- **Budget exhaustion has its own outcomes** (`FAILURE_BUDGET_EXHAUSTED`, `CALL_BUDGET_EXHAUSTED`) with fixed messages, never `ABORTED`, so the dispatcher ending a task is never confused with the model giving up.
- **The system text and tool schema still ask for a `DONE` message, but the validator tolerates its absence.** A missing or blank `DONE` message gives the operator `prompts.DONE_DEFAULT_MESSAGE`, and the previous-task slot shows that same operator text, as it does for every outcome. A completed task should not cost a schema retry or end `LLM_INVALID` over a missing summary. The prompt surface is unchanged on purpose, so the registry hash stays the same; the logged `plan` record keeps the raw `message` (null or blank), so a default can be told from a model-written text. Rejected: making `message` optional for `DONE` in the system text and schema (changes the hash and invites empty `DONE`s), and keeping the check strict. `ABORT` stays strict: its message carries the reason or the clarifying question.
- **Ambiguous instructions** ("go to the chair" with two chairs) end with `ABORT` and a clarifying question in `message`. A dedicated `ASK` status is planned for v2 ([roadmap.md](../../docs/roadmap.md#ask-plan-status)).
- **Fixed slots, not a conversation.** Context size depends only on the current task, which keeps token and latency figures comparable across conditions. Rejected: carrying a conversation, and compaction (OpenClaw's context grows with the session and sits outside experimental control, which is part of why it was replaced: [architecture.md](../../docs/architecture.md#why-a-purpose-built-dispatcher)).
- **Body sections render as indented `Heading: text` lines after the parameters, with no dash.** The dash marks parameters, so the two kinds stay apart; every skill gets the same four lines, so catalogs of different granularity differ only in content. Continuation lines are indented rather than joined, so section text reaches the model verbatim. Rejected: Markdown `###` sub-headings per skill, which roughly double the catalog's line count for the same content.
- **Static content first** (tools, system text, catalog), so prompt caching could be added later without reordering.
- **No prompt caching.** Cached tokens are reported separately, and cache behaviour differs between skill sets of different sizes, which would confound token comparisons between granularity conditions. Kept as a future idea ([roadmap.md](../../docs/roadmap.md#future-ideas)).
- **Executed-steps history keeps the last K entries plus an "n earlier entries omitted" line**, with no model summarisation. The count line tells the model that history was cut; summarisation would add an uncontrolled LLM step.
- **After a failure, leftover steps are shown as `[abandoned]`, not cleared**, so the model does not have to re-derive them blind.
- **Notices are fixed texts that tell the model directly what to do** ("failure f of N … revise or ABORT"). Directive text steers behaviour, so the wording must be identical across experimental conditions. The current wording is a draft to be frozen before experiments ([roadmap.md](../../docs/roadmap.md#open-questions)).
- **A previous-task slot carries continuity between messages** (the robot remembers its posture). Posture and obstacle distances are stated from sampled state, never inferred from the last skill, because a skill can fail or be killed mid-motion.
- **Each task's first plan requires a fresh state read at task start; a failed read ends the task `STATE_UNAVAILABLE` before any LLM call.** Between tasks the robot can be moved by hand or by remote, and the obstacle distances the model plans walks against go stale; the previous task's last state can be minutes old. A read through the robot service is a socket round trip to a message already in memory, so it costs nothing next to an LLM call. Replanning rounds keep the latest `state_after`, which is already fresh. The read takes the latest message, not the first one after the request: at the assumed 50 Hz it is at most tens of ms old, and its age is logged. `STATE_UNAVAILABLE` is its own outcome because neither the model (`LLM_ERROR`) nor a dispatcher bug (`INTERNAL_ERROR`) caused it. Rejected: carrying the state over between tasks with no read (stale first plans); falling back to the carried-over state when the read fails, because the skills sample `state_before` and `state_after` through the same service, so every step would then fail its state read too, and planning on minutes-old obstacle distances is what the fresh read exists to prevent.
- **Every robot service loss ends the task, with no replan.** The model cannot fix an infrastructure fault, so the call would be wasted spend; ending the task also keeps crashes visible during development and keeps infrastructure noise out of the replan metrics in the study data. The interrupted step still counts as a failure. Rejected: replanning after a confirmed stop and a fresh read (the costs above).
- **The service restarts at the next task's fresh read, not at the loss, and a step never starts it.** The task-start read already starts a dead service and already turns a failed start into `STATE_UNAVAILABLE`, so no other restart path is needed, and one task uses one service process. Rejected: restarting right after the loss (saves about 1 s on the next prompt, adds code and a failure mode between tasks); a step's `arm` starting a dead service (a restart inside a task, with the state stream already ended).
- **`ROBOT_SERVICE_LOST` is its own outcome, not `STATE_UNAVAILABLE`.** `STATE_UNAVAILABLE` means the task never started planning because the task-start read failed; `ROBOT_SERVICE_LOST` means the control service died during a task that was already moving the robot, which is a safety event the study counts separately (with `stop_move.service_error` saying why). Its detail says whether the fallback stop was confirmed. Rejected: reusing `STATE_UNAVAILABLE` with a detail, which fits only the failed-read case and hides service losses in the task-start failure count.
- **A `STATE_UNAVAILABLE` ending always sends `StopMove`.** With the state unknown the dispatcher cannot confirm the robot is stationary (a previous task's `StopMove` may have failed, and a `Move` that stops arriving is assumed to leave the robot moving), and `StopMove` is harmless on a stationary robot. It runs the normal stop path, whose fresh-process fallback has its own DDS, so it works while the robot service is down. This also covers an operator stop that arrived during the read. Rejected: no `StopMove` because no step ran in the task, which would leave a stop request without a `StopMove`.
- **The model sees per-direction obstacle distances, not only posture.** Without them the model plans walks blind and learns of an obstacle only from a failed or killed step; with them it can shorten a walk, ask, or answer "how far is the wall" from data. Four numbers on one line cost a few tokens and keep the section's shape fixed (`unavailable` instead of a dropped line, so the model is told the data is missing). The guidance sits in the system text, not the Robot section, so it is stated once per request and covered by the registry hash. Rejected: a derived "path clear / blocked" flag, which would hide the sensor's limits behind a yes/no the sensor cannot back.
- **A fixed walk margin (distance minus 0.3 m, *tunable*) instead of trusting the raw value.** The value is the nearest return in a direction, the walk is open-loop and the body has length, so walking the full reported distance would end at or in the obstacle. A fixed rule in the system text gives the same behaviour across models and conditions. Shortfalls use the existing statuses (shorter walk with a reason, or `ABORT`) rather than a new one. The margin is not a safety mechanism; the motion budget, StopMove and the operator remain that ([safety.md](../../docs/safety.md)).
- **`range_obstacle_raw` and the rest of the state stay log-only.** The raw array has an assumed order and sentinel values that `obstacle_m` already resolves; showing both would let the model second-guess the conversion and spend tokens on numbers it cannot interpret. The full state is in `state_before` / `state_after` of every step for analysis ([run-log.md](run-log.md)).
- **Nothing enters the context that the dispatcher did not shape.** No stderr, tracebacks, raw stdout or provider error text; every problem reaches the model as one short line. This keeps the context bounded and identical in form across conditions.
- **Step bounds know only `number`, `string` and `enum`.** No skill declared an `integer` param, and its extra rule (integral floats converted to int) was untested surface in both the registry and the bounds check, so it was dropped. A skill that needs whole numbers declares `number` and rounds or rejects in its own code. Rejected: keeping `integer` for future skills; it can be added back with its first user.
- **Plan validation runs in the loop, not in the planner.** A `PlannerClient` returns the reply unvalidated (`LLMResult.tool_input`, `None` without a `submit_plan` call), and the loop runs `llm.check_reply` on it. Every planner (the Anthropic client, the test planner, a future provider) then gets the same checks without repeating them, and the planner needs no horizon. Rejected: validating inside each planner adapter, which every adapter had to copy.
- **`prompts.py` holds every fixed wording the model reads and every operator-facing task text; nothing else.** For the model: the system text, user-message sections, notices, the rejection section, plan-validation errors, step-bounds errors, the malformed-response and step-kill messages, and the not-dispatched prefix. For the operator: outcome messages, Telegram replies, the stop acknowledgement (one `STOPPING` text for Telegram and the CLI), and the outcome text's step-count and omitted-lines lines. `MOTION_BUDGET_MESSAGE` is defined in `budget.py` and re-exported. Pure layout stays with its renderer (`N. call -> outcome: message`, `[pending]`, `{loc}: {msg}`, `{outcome}: {message}`), as does the `submit_plan` tool schema in `llm.py`, which is covered by the registry hash. Console diagnostics and start-up or usage errors (`Config error:`, the lock message, missing API key or token, run-log write failures, `go2 catalog` headers, the batch `Task {i}:` line) stay in their modules: they are not study inputs. `prompts.py` imports `models` only for type checking, so `models.py` can take the kill messages from it. Rejected: moving every string, diagnostics included, which would bury the texts that matter for comparability among ones that do not.
- **Operator-message arguments are chosen in `prompts.py`.** The loop hands `prompts.operator_message` one typed `OutcomeFacts` (run id, limits, failure count, last failure, the model's message, LLM error detail, exception type), and `prompts.py` picks the values each template needs, next to the templates themselves. Rejected: typed fields on `TaskOutcome`, which would put formatting inputs (config limits, error detail) into the result every transport receives.
- **A task end is raised, not returned.** Each ending inside the loop raises `_TaskEnded` carrying an `_Ending` (the `_end` arguments), after `StopMove` where one is due, and `run_task` handles it once by calling `_end`. The loop methods then return plain values (`LLMResult`, `Plan`, `Feedback`, `StepResult`) and no caller checks for an outcome. Rejected: typed result objects, which keep every propagation check (Python has no `?` operator); a context-manager task scope whose exit calls `_end`, which conflicts with the `INTERNAL_ERROR` handling and hides the `StopMove`-then-`_end` order; calling `_end` at the raise site, which would write a second `task_end` if anything raised afterwards.
- **The context input and the step result are filled from their source structs by field name.** `ContextInput` takes every field `_Task` holds under the same name, and `StepResult` takes every `ExecResult` field except `interrupt_cause` and `stop_move` (logged as its own record, [run-log.md](run-log.md)); only the remaining fields are listed. A field added to both sides needs no copy line. Rejected: one shared struct, which would mix per-task mutable state with the frozen snapshot the renderer reads.
