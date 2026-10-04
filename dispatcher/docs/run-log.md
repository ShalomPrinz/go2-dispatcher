# Run log

Every task writes a complete JSONL log. It is the dataset for the study ([project.md](../../docs/project.md)): tokens, latency and replanning are all computed from it, and it holds the exact prompts and every robot state sample. The record types and their fields are a contract: analysis scripts depend on them, so a change here is a change to the dataset.

What counts as a failure, an LLM call or a rejection is defined in [loop-and-context.md](loop-and-context.md#what-counts); this page says where each is recorded.

## Files

- One file per task: `{log.dir}/{YYYYMMDDTHHMMSS}_{run_id[:8]}.jsonl`, using the local time at task start. Example: `runs/20261001T113603_b6bb52fe.jsonl`.
- `{log.dir}/index.jsonl`: one line per finished task, including the experimental `condition`, `model` and `thinking`.
- `{log.dir}/.dispatcher.lock`: the single-instance lock (not a log).
- `{log.dir}/.stub_state.json`: the stub state, at the default `stub.state_file` (not a log).

`run_id` is a uuid4 hex per task. `session_id` is a uuid4 hex per process (one CLI invocation, one bot run); it links the tasks of one session.

Each line is written and flushed while a lock is held, so a crash leaves a usable partial log, and the `stop_requested` record written from the transport thread cannot interleave with other lines.

## Record order

A task's file starts with `task_start` and ends with `task_end`. In between, for each LLM call: `llm_request`, any `llm_retry`, then `llm_response` (followed by `plan` or `plan_invalid`) or `llm_error` / `llm_interrupted`. For each step of a valid plan: `step_start` and `step_result` for a dispatched step, only `step_result` for a step rejected by the pre-check, and a `stop_move` after the `step_result` of a killed step. `stop_requested` can appear anywhere (it is written from the transport thread). A stop or time limit that arrives while no step runs adds a `stop_move` just before `task_end`. An internal error adds `exception` and a `stop_move`.

## Envelope

Every line is one JSON object with these keys first:

| Key | Meaning |
|---|---|
| `ts` | ISO 8601 local time with offset |
| `t_mono_ms` | milliseconds since task start (monotonic) |
| `session_id` | process session |
| `run_id` | task |
| `seq` | 0-based line number within the file, strictly increasing |
| `type` | record type (below) |

The payload fields follow.

## Record types

### `task_start`

`task`, `source` (`cli` / `telegram` / `test`), `sender_id` (Telegram user id or null), `condition`, `config` (full config dump; it holds no secrets), `registry_hash`, `system_text`, `catalog_text`, `tool_schema`, `skills` (names), `previous_task` (`TaskSummary` or null), `posture`, `versions` (`python`, `anthropic`, `pydantic`, `go2-dispatcher`), `git_commit` (`git rev-parse HEAD` in the base dir, or null). `versions` and `git_commit` are read once per process at startup.

### `llm_request`

`call_index` (1-based), `return_reason` (`initial` / `plan_complete` / `checkpoint` / `failure` / `schema_retry`), `retry_of` (for `schema_retry`: the reason being retried; otherwise null), `user_text` (the exact user message).

### `llm_retry`

An infra retry (not an LLM call): `call_index`, `attempt` (1-based number of the attempt that failed), `error_type`, `status_code` (null for connection errors), `attempt_latency_ms`, `sleep_s`.

### `llm_response`

`call_index`, `latency_ms` (successful attempt only), `total_ms` (whole call including failed attempts and backoff), `attempts`, `stop_reason`, `usage` (verbatim from the API), `content` (all response blocks, including any `thinking` or `text` blocks; only the first `submit_plan` `tool_use` block is used), `response_id`, `request_id`.

### `llm_error` / `llm_interrupted`

`llm_error`: `call_index`, `detail` (for example `"BadRequestError 400"`). The task ends `LLM_ERROR`.
`llm_interrupted`: `call_index`, `cause` (`operator` / `task_time_limit`). A stop or the deadline cut the retries short.

### `plan`

A valid plan: `call_index`, `plan` (`status`, `steps`, `replan_after`, `message`), `stop_at` (`replan_after` or the number of steps).

### `plan_invalid`

An invalid reply: `call_index`, `tool_input`, `rejection_kind` (`horizon` / `schema` / `semantic` / `no_tool_call` / `max_tokens`), `steps_in_plan` and `horizon` (both null unless `rejection_kind` is `horizon`), `errors` (all errors, horizon first).

### `step_start`

`ref` (the step's `StepRef`: `call_index`, `plan_step`, `skill`, `params` (filled)) and `dispatch` (the step's `StepDispatch`: `index` (task-wide dispatched count), `timeout_s`, `motion_cost`, `fault`). Both objects appear unchanged in the step's `step_result`.

### `step_result`

All `StepResult` fields at the top level, with the step's identity nested under `ref` and the dispatch fields under `dispatch`: `ref` (an object with `call_index`, `plan_step`, `skill`, `params`: filled and normalised for a dispatched step or a motion-budget rejection, raw as received for a bounds rejection), `dispatch` (an object with `index` (task-wide dispatched count), `timeout_s`, `motion_cost`, `fault`; null for a rejected step), `outcome`, `error_code`, `error_message`, `response` (full `SkillResponse` with `state_before` / `state_after` / `timing`), `duration_ms`, `exit_code`, `pid`, `stderr_tail`, `verification` (always `"unverified"`). It also adds `budget_used`, `failures` and `posture` **after** this step has been counted. It is written for both dispatched and rejected steps.

### `stop_requested`

`source` (`cli` / `telegram` / `shutdown`), `during` (`llm_call` / `step` / `between`).

### `stop_move`

All `StopMoveResult` fields: `ok`, `reason` (`operator` / `task_time_limit` / `step_timeout` / `shutdown` / `internal_error`), `duration_ms`, `exit_code`, `response` (its `timing.stop_call_ms` and `state_after`), `stderr_tail`. It is the only record of a StopMove: written right after the `step_result` of a killed step, and for every StopMove the dispatcher sends itself. If the utility process could not even be started, `ok` is false, `exit_code` and `response` are null, and `stderr_tail` holds `"<ExceptionType>: <message>"` (StopMove never raises).

### `exception`

`where` (`run_task`), `exception_type`, `message`, `traceback`. The task ends `INTERNAL_ERROR`; `task_end` and the index line are still written, even if writing this record, killing the skill or the StopMove fails. The exception class is stored as `exception_type`, not `type`, because `type` is the record type.

### `task_end`

`outcome`, `message`, `llm_calls`, `failures`, `steps_recorded`, `steps_dispatched`, `rejections` (non-dispatched steps), `horizon_rejections`, `usage_totals` (sum of every top-level numeric `usage` field over all responses), `budget_used`, `stop_move_failed`, `final_posture`, `duration_ms`.

## `index.jsonl`

One line per finished task, without the envelope:

```json
{"run_id":"b6bb52fe55bb41589a5a1fc51c463f72","file":"20261001T113603_b6bb52fe.jsonl","ts_start":"2026-10-01T11:36:03.636693+03:00","task":"turn left 90 degrees, then tell me if you see a chair","source":"cli","outcome":"DONE","condition":"","backend":"stub","model":"claude-sonnet-5-5","thinking":"between_tools","planning_horizon":5,"max_llm_calls":20,"registry_hash":"bf06b5af6abd480b","llm_calls":2,"failures":0,"steps_dispatched":2,"input_tokens":200,"output_tokens":40,"duration_ms":156.1}
```

`file` is relative to `log.dir`. A task whose log could not be opened has no index line. `input_tokens` and `output_tokens` are the sums of those two `usage` fields only; any other numeric `usage` fields (for example cache counters) are summed in `task_end.usage_totals`. Use the index to select tasks by condition, then read their files for per-call detail.

## Computing the study metrics

Group tasks by condition first: `condition`, `registry_hash` (the skill set and prompt surface), `model`, `thinking`, `planning_horizon` and `max_llm_calls` are in every index line; the full config is in `task_start.config`. Runs with different registry hashes saw different prompts and are not directly comparable.

| Metric | How to compute it |
|---|---|
| Task outcome, success rate | `index.jsonl` `outcome` (`DONE` = success); outcome codes in [loop-and-context.md](loop-and-context.md#task-outcomes) |
| Tokens per task | `index.jsonl` `input_tokens` / `output_tokens`, or `task_end.usage_totals` (all numeric usage fields) |
| Tokens per call | `llm_response.usage` |
| LLM calls per task | `index.jsonl` `llm_calls` = number of `llm_response` records (schema retries included, infra retries not) |
| Replanning frequency | count `llm_request` by `return_reason`. Replans are the calls after the first: `failure` (stop-and-report after a failed or rejected step), `checkpoint` (the model asked to review), `plan_complete` (the plan ran out before the task was done). Report `schema_retry` separately: it re-asks the same question after an invalid reply. At horizon 1 every step ends in `plan_complete`, so compare reasons, not just counts |
| LLM latency per call | `llm_response.latency_ms` (successful attempt only) and `total_ms` (including failed attempts and backoff) |
| Infrastructure retries | `llm_retry` records (`error_type`, `status_code`, `attempt_latency_ms`, `sleep_s`); `llm_response.attempts` |
| Task latency | `index.jsonl` / `task_end` `duration_ms` |
| Failures and rejections | `task_end.failures`, `task_end.rejections` (pre-check rejections only); per step `step_result.outcome` and `error_code` |
| Invalid replies | `plan_invalid` by `rejection_kind`; horizon rejection rate = `plan_invalid` with `rejection_kind == "horizon"` ÷ `llm_response` records (monitoring rule in [llm.md](llm.md#horizon-rejection)) |
| Skill wall time | `step_result.duration_ms` (whole subprocess) |
| Process overhead per step | `step_result.duration_ms − response.timing.total_ms` (interpreter start and teardown outside the skill's own timing) |
| Stop latency | `stop_move.response.timing.stop_call_ms` (utility start to `StopMove()` return) and `stop_move.duration_ms` (whole utility process) |
| Motion commanded | `step_start.dispatch.motion_cost`, `task_end.budget_used` |
| Robot state | `step_result.response.state_before` / `state_after`; `stop_move.response.state_after` ([robot.md](../../skills/docs/robot.md#state-sampling)) |
| Exact prompts | `task_start.system_text` / `catalog_text` / `tool_schema` + `llm_request.user_text` |
| Session continuity | `session_id` links tasks of one process; `task_start.previous_task` and `posture` show what carried over |

## Design decisions

- **Records are built only in `runlog.py`, by one named method per record type** (`RunLog.task_start`, `step_result`, `stop_move`, ...; the index row in `RunLogFactory.append_index`). The loop passes per-record data, mostly existing domain objects (`StepResult`, `StopMoveResult`, `LLMResult`, `PlanCheck`, `TaskOutcome`), and never builds a record itself, so the record contract lives in one module. Each method writes exactly one record. The session-constant `task_start` fields (config, condition, prompt surface, registry hash, skills, versions, git commit) are collected once per process into `SessionInfo`. Rejected: one pydantic model per record type (`log.write(StepResultRecord(...))`), which only moves the long field lists into constructors.
- **The prompt surface is owned by the dispatcher and only copied into the log.** `context.PromptSurface.build` builds the system blocks, catalog text, tool schema, registry hash and skill names once per process; the transport setup passes it to the planner (which sends it, [llm.md](llm.md#design-decisions)), and `go2 catalog` uses the same builder. `Dispatcher.__init__` builds its own `RunLogFactory` with a fresh `session_id` and `SessionInfo.collect(cfg, planner.surface)`, so what is logged is what is sent by construction, and there is one `session_id` per dispatcher (one per process). Rejected: callers building the factory from a separately passed surface with a runtime equality check in the dispatcher, which let the two disagree until startup; and the dispatcher reading the surface from the run-log session, which made the logger the owner of what the model is sent.
- **A step is a `StepRef` plus an optional `StepDispatch`, composed in `StepResult`.** `StepRef` (`call_index`, `plan_step`, `skill`, `params`) is what every recorded step has; `StepDispatch` (`index`, `timeout_s`, `motion_cost`, `fault`) is what the dispatcher decided for a dispatched step. The loop builds both once per dispatched step (the pre-check builds the `StepRef` of a rejection), passes them to `RunLog.step_start` and stores them on the `StepResult`, so `step_start` and `step_result` carry the same nested `ref` and `dispatch` objects; a rejected step has `dispatch = null`. Composition keeps the dispatched fields strictly typed (`index` and `timeout_s` are never null) and the step identity built in one place instead of being passed field by field. Rejected: a flat `StepResult` with the identity and dispatch fields at the top level, which made `step_start` take the fields one by one and re-flatten the dispatch object; inheritance `StepResult(StepRef, StepDispatch)`, which would force the dispatch fields optional because rejected steps are also `StepResult`s.
- **The loop resolves a step's stub fault while building its `StepDispatch`.** It calls `cfg.stub.fault_at(index)` with the task-wide dispatched index; config validation already rejects `stub.faults` under the real backend and keeps one fault per step, so the loop does not re-check the backend. The fault belongs to `StepDispatch` because `step_start` records it before the executor runs. Rejected: resolving the fault in the executor, which would need the dispatched index passed in and the fault handed back for the log.
- **One file per task plus an index.** A task is the unit of analysis; the index lets analysis select tasks by condition without parsing every file.
- **Every line is flushed as it is written**, under a lock, so a crash leaves a usable partial log and lines from the transport thread never interleave.
- **The exact prompt surface is logged with every task** (system text, catalog, tool schema, registry hash) and the exact user message with every call, so any call can be reconstructed and conditions can be told apart by hash.
- **The return reason is logged on every call.** Without it, horizon 1 would look like constant replanning; with it, `plan_complete` calls can be separated from replans after a failure ([loop-and-context.md](loop-and-context.md#return-reasons)).
- **Horizon rejections are a `plan_invalid` kind** (`rejection_kind == "horizon"`, with `steps_in_plan` and `horizon`), not a record of their own: one record per invalid reply, and the rate is still a simple filter per condition ([llm.md](llm.md#horizon-rejection)). The two fields are always present (null for other kinds) so every `plan_invalid` has the same keys.
- **One place per StopMove result**: the `stop_move` record. A killed step's `step_result` does not repeat it; the `stop_move` record follows it directly.
- **Infrastructure retries are logged separately and never counted as LLM calls or replans**, so provider trouble does not contaminate the replanning metric.
- **Full robot state is logged, not just posture**, as the data for setting v2 verification thresholds ([roadmap.md](../../docs/roadmap.md#v2-plan)).
