# Run log

Every task writes a complete JSONL log. It is the dataset for the study ([project.md](../../docs/project.md)): tokens, latency and replanning are all computed from it, and it holds the exact prompts and every robot state sample. The record types and their fields are a contract: analysis scripts depend on them, so a change here is a change to the dataset.

What counts as a failure, an LLM call or a rejection is defined in [loop-and-context.md](loop-and-context.md#what-counts); this page says where each is recorded.

## Files

`log.dir` (default `logs`) is the root. Each task is written to the folder `{log.dir}/{llm.provider}/{robot.backend}/`, using the config values verbatim, so there are four folders: `anthropic/stub`, `anthropic/real`, `openrouter/stub`, `openrouter/real`. The folder is derived from the config (`Config.run_log_dir`) and cannot be set by hand.

- One file per task: `{folder}/{YYYYMMDDTHHMMSS}_{run_id[:8]}.jsonl`, using the local time at task start. Example: `logs/anthropic/stub/20261001T113603_b6bb52fe.jsonl`.
- `{folder}/{YYYYMMDDTHHMMSS}_{run_id[:8]}.state_stream.jsonl`: the task's robot state stream ([below](#state_streamjsonl)), beside its log file. It is not a task log: select task logs through `index.jsonl`, not by globbing `*.jsonl`.
- `{folder}/index.jsonl`: one line per finished task in that folder, including the experimental `condition`, `provider`, `model` and `thinking`.
- `{log.dir}/.dispatcher.lock`: the single-instance lock, at the root so it covers every folder (not a log).
- `{log.dir}/robot.sock` and `{log.dir}/robot.lock`: the robot service's socket and lock ([running.md](../../docs/running.md#robot-service); not logs).
- `{log.dir}/robot_service.log`: the robot service's stderr and the dispatcher's notes on starting, restarting and stopping it, for diagnosis only, like the vision service log.
- `{log.dir}/vision_service.log`: the vision service's stderr and the dispatcher's notes on starting, restarting and stopping it, for diagnosis only (not study data, no records; [running.md](../../docs/running.md#vision-service)).

**Research data is `logs/anthropic/real/` only.** Analysis reads one folder and its `index.jsonl`; it never globs across folders, so stub runs and OpenRouter runs cannot leak into the dataset. The other folders are for development and testing.

`run_id` is a uuid4 hex per task. `session_id` is a uuid4 hex per process (one CLI invocation, one bot run); it links the tasks of one session.

Each line is written and flushed while a lock is held, so a crash leaves a usable partial log, and the `stop_requested` record written from the transport thread cannot interleave with other lines.

## Record order

A task's file starts with `task_start` and ends with `task_end`. In between, for each LLM call: `llm_request`, any `llm_retry`, then `llm_response` (followed by `plan` or `plan_invalid`) or `llm_error` / `llm_interrupted`. For each step of a valid plan: `step_start` and `step_result` for a dispatched step, only `step_result` for a step rejected by the pre-check, and a `stop_move` after the `step_result` of a killed step. `stop_requested` can appear anywhere (it is written from the transport thread). A stop or time limit that arrives while no step runs adds a `stop_move` just before `task_end`. An internal error adds `exception` and a `stop_move`. A failed task-start state read adds only a `stop_move` before `task_end`. A failed pre-step state read adds a `stop_move` before `task_end`, with no `step_start` for that step (reason `robot_service_lost` when the service was dead or unreachable, else `state_unavailable`); a precondition violation adds `precondition_failed` before `task_end`, also with no `step_start`.

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

`task`, `source` (`cli` / `telegram` / `test`), `sender_id` (Telegram user id or null), `condition`, `config` (full config dump; it holds no secrets), `registry_hash`, `system_text`, `catalog_text`, `tool_schema`, `skills` (names), `previous_task` (`TaskSummary` or null), `posture`, `robot_io`, `state_age_ms`, `state_error`, `versions` (`python`, `anthropic`, `httpx`, `pydantic`, `go2-dispatcher`), `git_commit` (`git rev-parse HEAD` in the base dir, or null). `versions` and `git_commit` are read once per process at startup.

- `robot_io`: how robot state reaches the dispatcher and the skills, `"service"` (through the robot service, [running.md](../../docs/running.md#robot-service)). Set by the code, not by config. A record without `robot_io` ran with per-process DDS: each skill process created its own DDS participant to sample state, so its `timing.state_ms` includes DDS init, subscription and the first message (about 170–190 ms on the robot); its tasks started from the state carried over from the previous task, with no fresh read. Never mix `robot_io` values within one experiment.
- `posture` is from the state read through the robot service at task start, the state the first plan sees; `unknown` when the read failed.
- `state_age_ms`: the age of that state's message at the read (the service's `age_ms`); null when the read failed.
- `state_error`: why the read failed (one line, for example `RobotServiceError: Robot service exited with code 1 before ready (see ...)` or `link down; ...`); null when it succeeded. A failed read ends the task `STATE_UNAVAILABLE` with no LLM call: a `stop_move` (reason `state_unavailable`) and `task_end` follow (after a `stop_requested`, if any), and no state stream is written.

### `llm_request`

`call_index` (1-based), `return_reason` (`initial` / `plan_complete` / `checkpoint` / `failure` / `schema_retry`), `retry_of` (for `schema_retry`: the reason being retried; otherwise null), `user_text` (the exact user message).

### `llm_retry`

An infra retry (not an LLM call): `call_index`, `attempt` (1-based number of the attempt that failed), `error_type`, `status_code` (null for connection errors), `attempt_latency_ms`, `sleep_s`.

### `llm_response`

`call_index`, `latency_ms` (successful attempt only), `total_ms` (whole call including failed attempts and backoff), `attempts`, `stop_reason`, `usage` (verbatim from the API), `content` (all response blocks, including any `thinking` or `text` blocks; only the first `submit_plan` `tool_use` block is used), `response_id`, `request_id`.

With `llm.provider = "openrouter"` the same fields hold OpenRouter's native values ([llm.md](llm.md#response-handling)): `stop_reason` is `finish_reason` (`tool_calls`, `stop`, `length`, ...), `usage` is OpenRouter's usage object (`prompt_tokens`, `completion_tokens`, `total_tokens`, ...), `content` is the full `choices` list (each with `message`, `finish_reason`, `native_finish_reason`), `response_id` is the generation id and `request_id` is null.

### `llm_error` / `llm_interrupted`

`llm_error`: `call_index`, `detail` (for example `"BadRequestError 400"`). The task ends `LLM_ERROR`.
`llm_interrupted`: `call_index`, `cause` (`operator` / `task_time_limit`). A stop or the deadline cut the retries short.

### `plan`

A valid plan: `call_index`, `plan` (`status`: `PLAN`, `DONE`, `ABORT` or `ASK`; `steps`, `replan_after`, `message`), `stop_at` (`replan_after` or the number of steps).

### `plan_invalid`

An invalid reply: `call_index`, `tool_input`, `rejection_kind` (`horizon` / `schema` / `semantic` / `no_tool_call` / `max_tokens`), `steps_in_plan` and `horizon` (both null unless `rejection_kind` is `horizon`), `errors` (all errors, horizon first).

### `step_start`

`ref` (the step's `StepRef`: `call_index`, `plan_step`, `skill`, `params` (filled)) and `dispatch` (the step's `StepDispatch`: `index` (task-wide dispatched count), `timeout_s`, `motion_cost`, `fault`). Both objects appear unchanged in the step's `step_result`.

### `step_result`

All `StepResult` fields at the top level, with the step's identity nested under `ref` and the dispatch fields under `dispatch`: `ref` (an object with `call_index`, `plan_step`, `skill`, `params`: filled and normalised for a dispatched step or a motion-budget rejection, raw as received for a bounds rejection), `dispatch` (an object with `index` (task-wide dispatched count), `timeout_s`, `motion_cost`, `fault`; null for a rejected step), `outcome`, `error_code`, `error_message`, `response` (full `SkillResponse` with `state_before` / `state_after` / `timing`), `duration_ms`, `exit_code`, `pid`, `stderr_tail`, `verification` (`verified` / `failed` / `unverified` / `unavailable`, null when not checked: the step was not dispatched, or its outcome is not `ok`; [loop-and-context.md](loop-and-context.md#verification)), `faults` (the faults detected in the response: `fallen`, `fault_mode`, `command_rejected`; empty when none, and always empty for a step that was not dispatched; detected on every dispatched step, also one with a null verdict). It also adds `budget_used`, `failures` and `posture` **after** this step has been counted. It is written for both dispatched and rejected steps.

### `precondition_failed`

A step not dispatched because the robot's state did not meet the skill's `requires` ([loop-and-context.md](loop-and-context.md#preconditions)): `ref` (the step's `StepRef`, params filled), `requires` (the allowed postures), `state` (the full `RobotState` of the pre-step read the check used; its `posture` is the observed one). The task then ends `PRECONDITION_FAILED`. The step gets no `step_result` and no dispatch index.

### `stop_requested`

`source` (`cli` / `telegram` / `shutdown`), `during` (`starting` / `llm_call` / `step` / `between`). `starting` is a stop that arrived during the task-start state read, before the log opened; it is written right after `task_start`.

### `stop_move`

All `StopMoveResult` fields, one run of the [stop path](../../docs/safety.md#the-stop-path):

- `ok`, `reason` (`operator` / `task_time_limit` / `step_timeout` / `shutdown` / `internal_error` / `state_unavailable` / `robot_service_lost` / `service_stop_failed`). `state_unavailable`: a task-start state read failed, or a pre-step read found no state message or the link down. `robot_service_lost` also covers a pre-step read that found the service dead or unreachable. `service_stop_failed`: the `disarm` after a step the executor did not stop reported that a `StopMove` the robot service sent on its own failed, so the fallback ran ([loop-and-context.md](loop-and-context.md#stop-failed)).
- `stop_path`: `service` (the robot service's `stop` answered `stop_sent` with code 0) or `fallback` (the fresh-process `stop_move` ran). Null only when the stop path itself raised (a test fake; the executor never raises).
- `duration_ms`: the whole stop path, a failed service stop included. `kill_to_stop_ms`: measured by the dispatcher from the skill kill to the confirmed (or failed) stop; null when no step was killed.
- `stop_call_ms`: the service's `StopMove` call time from `stop_sent`, or from the `disarm` reply's `stop_failed`; null on a fallback unless the service reported a failed `StopMove` (a non-zero `stop_sent`, or `stop_failed`).
- `service_error`: why the fallback ran (`ServiceUnavailable: no reply within 0.5s`, `StopMove returned 3104`, ...); null on the service path. After a `stop_failed` it is `StopMove returned {ret} ({reason})`, with the service's reason for that `StopMove` (`move done`, `lease deadline`, `lease connection closed`, `disarmed`) and `ret` `None` if the call raised.
- `service_lost`: true when the robot service was lost on this stop (hung, dead or no reply) and was SIGKILLed before the fallback; set on every loss path, whatever `reason` the stop was for (a loss during an operator or timeout stop keeps that `reason`). False on the service path and on a fallback after a non-zero `stop_sent` or a `stop_failed`, where the service is kept.
- `state_after`: the state after the stop, read through the service on the service path and the utility's `state_after` on the fallback; null if the read failed.
- Fallback only: `exit_code`, `response` (its `timing`), `stderr_tail`. If the utility process could not even be started, `ok` is false, `exit_code` and `response` are null, and `stderr_tail` holds `"<ExceptionType>: <message>"`.

It is the only record of a stop: written right after the `step_result` of a killed step or of a step during which the robot service was lost, and for every stop the dispatcher sends itself.

### `exception`

`where` (`run_task`), `exception_type`, `message`, `traceback`. The task ends `INTERNAL_ERROR`; `task_end` and the index line are still written, even if writing this record, killing the skill or the StopMove fails. The exception class is stored as `exception_type`, not `type`, because `type` is the record type.

### `task_end`

`outcome`, `message`, `llm_calls`, `failures`, `steps_recorded`, `steps_dispatched`, `rejections` (non-dispatched steps), `horizon_rejections`, `usage_totals` (sum of every top-level numeric `usage` field over all responses), `budget_used`, `stop_move_failed`, `final_posture`, `duration_ms`.

## How records are produced

Each record type has one frozen dataclass in `runlog/events.py`, named after it (`TaskStart`, `LlmRequest`, `LlmRetry`, `LlmResponse`, `LlmError`, `LlmInterrupted`, `PlanRecord` for `plan`, `PlanInvalid`, `StepStart`, `StepRecord` for `step_result`, `PreconditionFailed`, `StopRequested`, `StopMove`, `ExceptionRaised` for `exception`, `TaskEnd`). It carries the typed objects the record is made from (`StepResult`, `LLMResult`, `PlanCheck`, `TaskOutcome`, ...), never a pre-built dict. `TaskEvent` is the union of these 15; `Event` adds the two state-stream events `RobotSample(state, link, rx_mono)` and `RobotLink(link, rx_mono)`, which are not records.

The loop (`Dispatcher`) is the only emitter of task events. It calls `publish(event, log, observers)` (`runlog/sinks.py`, through `_Task.emit`), which writes the event to the log first and then hands it to each observer. `RunLog.emit` turns the event into its record with a `match` on the event class, one `case` per event and the serialisation in that case; it ends with `case _: assert_never(event)`, so a missing case is a type error (basedpyright) and an unknown type raises. `step_result` still comes from `StepResult.model_dump(mode="json")`, so the wrap serializer on `StepResult` stays the one place that flattens `process`. A log error propagates out of `publish` (and no observer sees that event); an observer error is swallowed. The log is `None` until the file opens, so the observers still see events (`exception`, `task_end`) when it cannot be opened.

### Adding a record type

1. A frozen dataclass in `runlog/events.py` and its member in `TaskEvent`.
2. One `case` in `RunLog.emit` (`runlog/writer.py`) that writes the record; basedpyright flags the `assert_never` until it exists.
3. The `t.emit(...)` call in `dispatcher.py`.
4. A `case` in `ConsoleTrace.emit` / `TelegramProgress.emit` if the event should be shown; otherwise they ignore it.
5. The record in [Record types](#record-types) and [Record order](#record-order).
6. A unit test that pins its keys, in order, and values through the real writer (as in `dispatcher/tests/unit/test_runlog_records.py`).

## Observers

`Dispatcher` receives its `RunLogFactory` (`runlog=`) and a sequence of `observers` (default none) from the composition root, `app.build_dispatcher`. An observer is an `Observer` (`runlog/sinks.py`): one `emit(event: Event)` with a `match` that has cases only for the events it presents and `case _: pass` for the rest. The log file content is the same with or without an observer. `build_dispatcher` takes one `observer` factory that builds the transport's observer from the registry: the CLI passes `ConsoleTrace`, its live trace ([running.md](../../docs/running.md#output)); the bot passes a function that builds `TelegramProgress`, its progress message ([running.md](../../docs/running.md#progress-message)), and keeps that instance for its handlers, so nothing reads an observer back from the dispatcher. The tests build the factory with `make_runlog` and set no observer unless they test one.

The [state stream](#state_streamjsonl) writer (`StateStream`, in `execution/state_stream.py`, the only emitter in `execution/`) takes the task's observers and calls `emit(RobotSample(...))` on its own thread for each `sample` line it writes, `emit(RobotLink(...))` for each `link` line, and `emit(RobotLink(link, None))` once with the subscription's link after `stream_start`. The stream file is written by the stream itself, not by `RunLog`. `ConsoleTrace` ignores these events; `TelegramProgress` shows the [robot line](../../docs/running.md#robot-line). An exception from an observer is caught in the stream writer and printed once per task as `Warning: state stream observer failed: {ExceptionType}.`; the stream and the task go on. They never run on the stop path: the stream thread is separate from the loop, the executor and `request_stop`.

## `state_stream.jsonl`

While a task runs, the dispatcher subscribes to the robot service and writes its state stream to the task's `.state_stream.jsonl` file, one JSON object per line, flushed per line. It starts after `task_start` and ends after `task_end` (the dispatcher waits at most `STREAM_JOIN_S`, 1 s, for it). Samples are downsampled to at most one per `STREAM_PERIOD_S` (0.1 s, 10 Hz, *tunable*): fast enough to show a walk's velocity and obstacle distances changing through a 1–2 s step and a link gap to within 0.1 s, and about 36 000 lines for an hour of tasks instead of five times that at the full assumed 50 Hz. The skills' own `state_before` / `state_after` are unaffected.

`t_mono_ms` is milliseconds since task start on the same monotonic clock as the task log's `t_mono_ms` (for samples and link events: the arrival of the message in the service), so stream lines line up with the task's records.

| `type` | Fields |
|---|---|
| `stream_start` | `t_mono_ms`, `period_ms`, `link` (`up` / `down`) at subscription |
| `sample` | `t_mono_ms`, `link`, `state` (a full `RobotState`, with `seq` and `age_ms`) |
| `link` | `t_mono_ms` (null if no message ever arrived), `link`: a change; for `down` the time of the last message before the gap, for `up` of the first one after it |
| `stream_error` | `t_mono_ms`, `detail` (`ExceptionType: message`); the stream ends |

A gap shows as a `link` `down` line, no samples, then a `link` `up` line ([service.md](../../services/robot/docs/service.md#robot-service)). The stream is off every critical path: it runs on its own thread and connection, and a failure of the stream (connection lost, file error) ends only the stream, never the task. One task uses one service process, so the stream covers the task, and a [service loss](loop-and-context.md#robot-service-lost) ends both: `stream_error` is the stream's last line.

## `index.jsonl`

One line per finished task, without the envelope:

```json
{"run_id":"b6bb52fe55bb41589a5a1fc51c463f72","file":"20261001T113603_b6bb52fe.jsonl","ts_start":"2026-10-01T11:36:03.636693+03:00","task":"turn left 90 degrees, then tell me if you see a chair","source":"cli","outcome":"DONE","condition":"","provider":"anthropic","backend":"stub","robot_io":"service","model":"claude-sonnet-5-5","thinking":"between_tools","planning_horizon":5,"max_llm_calls":20,"registry_hash":"bf06b5af6abd480b","llm_calls":2,"failures":0,"steps_dispatched":2,"input_tokens":200,"output_tokens":40,"duration_ms":156.1}
```

`file` is relative to the index's folder. A task whose log could not be opened has no index line. `provider`, `backend` and `model` are the config values; `robot_io` is as in `task_start` (absent in rows written without it); `thinking` is `llm.thinking` for `anthropic` and null for `openrouter`, which ignores it. `input_tokens` and `output_tokens` are the sums of those two `usage` fields only (for `openrouter`: of `prompt_tokens` and `completion_tokens`); any other numeric `usage` fields (for example cache counters) are summed in `task_end.usage_totals`. Use the index to select tasks by condition, then read their files for per-call detail.

## Computing the study metrics

Pick the folder first (`logs/anthropic/real/` for the study), then group its tasks by condition: `condition`, `registry_hash` (the skill set and prompt surface), `model`, `thinking`, `planning_horizon` and `max_llm_calls` are in every index line; the full config is in `task_start.config`. Runs with different registry hashes saw different prompts and are not directly comparable.

| Metric | How to compute it |
|---|---|
| Task outcome, success rate | `index.jsonl` `outcome` (`DONE` = success); outcome codes in [loop-and-context.md](loop-and-context.md#task-outcomes) |
| Clarifying questions | `index.jsonl` `outcome == "ASKED"`; the question is the last `plan` record's `plan.message` (`plan.status == "ASK"`) |
| Tokens per task | `index.jsonl` `input_tokens` / `output_tokens`, or `task_end.usage_totals` (all numeric usage fields) |
| Tokens per call | `llm_response.usage` |
| LLM calls per task | `index.jsonl` `llm_calls` = number of `llm_response` records (schema retries included, infra retries not) |
| Replanning frequency | count `llm_request` by `return_reason`. Replans are the calls after the first: `failure` (stop-and-report after a rejected plan or a failed step: a failing outcome, or outcome `ok` with verdict `failed`; the `step_result` before the call tells which), `checkpoint` (the model asked to review), `plan_complete` (the plan ran out before the task was done). Report `schema_retry` separately: it re-asks the same question after an invalid reply. At horizon 1 every step ends in `plan_complete`, so compare reasons, not just counts |
| LLM latency per call | `llm_response.latency_ms` (successful attempt only) and `total_ms` (including failed attempts and backoff) |
| Infrastructure retries | `llm_retry` records (`error_type`, `status_code`, `attempt_latency_ms`, `sleep_s`); `llm_response.attempts` |
| Task latency | `index.jsonl` / `task_end` `duration_ms` |
| Failures and rejections | `task_end.failures` (failing outcomes plus `ok` steps with verdict `failed`), `task_end.rejections` (pre-check rejections only); per step `step_result.outcome`, `error_code` and `verification` |
| Invalid replies | `plan_invalid` by `rejection_kind`; horizon rejection rate = `plan_invalid` with `rejection_kind == "horizon"` ÷ `llm_response` records (monitoring rule in [llm.md](llm.md#horizon-rejection)) |
| Skill wall time | `step_result.duration_ms` (whole subprocess) |
| Process overhead per step | `step_result.duration_ms − response.timing.total_ms` (interpreter start and teardown outside the skill's own timing) |
| Stop latency | `stop_move.kill_to_stop_ms` (kill to confirmed stop) by `stop_move.stop_path`. Service path: `stop_call_ms` is the `StopMove()` call in the service. Fallback: `dds_init_ms` + `client_init_ms` + `stop_call_ms` from `stop_move.response.timing`, and `duration_ms` (failed service stop plus the whole utility process). Records without `stop_path` all ran the fresh-process stop and have no `kill_to_stop_ms` ([safety.md](../../docs/safety.md#measured-latency)) |
| Robot service losses | `stop_move` with `service_lost == true` (`service_error` says why; `reason` is the stop's cause, which is `robot_service_lost` only when the loss itself triggered the stop); tasks ended by one: `outcome == "ROBOT_SERVICE_LOST"` |
| Failed stops | `stop_move` with `ok == false`; `task_end.stop_move_failed`; tasks ended by one with the service alive: `outcome == "STOP_FAILED"`. Failed `StopMove`s the robot service sent on its own: `stop_move` with `reason == "service_stop_failed"` (`ok` says whether the fallback then stopped the robot) |
| Motion commanded | `step_start.dispatch.motion_cost`, `task_end.budget_used` |
| Robot state | `step_result.response.state_before` / `state_after`; `stop_move.state_after` ([robot.md](../../services/robot/docs/robot.md#state-sampling)) |
| Exact prompts | `task_start.system_text` / `catalog_text` / `tool_schema` + `llm_request.user_text` |
| Session continuity | `session_id` links tasks of one process; `task_start.previous_task` shows what carried over |
| State at task start | `task_start.posture`, `state_age_ms`; failed reads: `task_end.outcome == "STATE_UNAVAILABLE"` and `task_start.state_error` (a pre-step read without a usable state has `state_error` null and a `stop_move` with reason `state_unavailable`) |
| Step verdicts | `step_result.verification` by `ref.skill`; why a step `failed`: `faults` (empty when the goal check failed). Null on rejected steps and on steps whose outcome is not `ok`, so verdict rates are over `ok` steps only; `faults` is still filled on failed steps (fault rate: over all dispatched steps). A verdict never changes `outcome`; a `failed` verdict on an `ok` step counts as a failure and leads to a `failure` call ([loop-and-context.md](loop-and-context.md#verification)). Replans caused by a verdict alone: `step_result` with `outcome == "ok"` and `verification == "failed"` |
| Precondition violations | `outcome == "PRECONDITION_FAILED"`; the step, its `requires` and the observed state in the `precondition_failed` record |
| Robot state over time | the task's `state_stream.jsonl`: samples at 10 Hz and link gaps |

### Latency report: `go2 report`

`go2 report [FOLDER]` computes the latency metrics above from one folder's existing records, read-only, one group per `model` + `registry_hash` + `condition`. What it selects, how it computes each number, what it skips and what it prints: [report.md](../../analysis/docs/report.md).

## Design decisions

- **Records are built only in `runlog/writer.py`, one `case` per event in `RunLog.emit`** (the index row in `RunLogFactory.append_index`). The loop emits per-record data, mostly existing domain objects (`StepResult`, `StopMoveResult`, `LLMResult`, `PlanCheck`, `TaskOutcome`), and never builds a record itself, so the record contract lives in one module. The file-format constants and `stream_path` live apart in `runlog/format.py`, which imports only `dispatcher.config`, so `analysis` reads them without reaching the run time. Each event writes exactly one record. The session-constant `task_start` fields (config, condition, prompt surface, registry hash, skills, versions, git commit) are collected once per process into `SessionInfo`, which keeps the typed `Config` (the index row reads `cfg.robot.backend`, `cfg.llm.model`, ...) and dumps it to JSON only when `task_start` is written. Rejected: one pydantic model per record type (`log.write(StepResultRecord(...))`), which only moves the long field lists into constructors.
- **Records are typed events with one `emit`, dispatched by an exhaustive `match`.** One frozen dataclass per record (`runlog/events.py`) replaces the 15 record methods that `RunLog`, the no-file log, the tee and each observer had to repeat with identical signatures. The writer's `match` ends in `assert_never`, so a record type without a writer case is a type error and an unknown object raises; observers match only what they show, with no empty methods. `publish(event, log, observers)` replaces the tee and the no-file log: `_Task.log` is `RunLog | None`, `None` until the file opens (or if opening fails), and `publish` is the one None check, so `_internal_error` and `_end` run unchanged. The index row is written only when `_Task.log` is set, because the row points at the task's log file, and `TaskOutcome.log_path` is empty otherwise. Events carry typed objects and the serialisation stays in the writer, so the record contract stays in one module. Rejected: `functools.singledispatchmethod` and a type-to-handler table, which give no exhaustiveness check; keeping one method per record, which needed a no-op class and a forwarding class kept in step with `RunLog` by hand.
- **The prompt surface is owned by the dispatcher and only copied into the log.** `context.build_prompt_surface` builds the system blocks, catalog text, tool schema, registry hash and skill names once per process; the transport setup passes it to the planner (which sends it, [llm.md](llm.md#design-decisions)), and `go2 catalog` uses the same builder. The composition root (`app.build_dispatcher`) builds the `RunLogFactory` with a fresh `session_id` and `SessionInfo.collect(cfg, planner.surface)` from the same planner it passes to `Dispatcher`, so the composition root guarantees that what is logged is what is sent, and there is one `session_id` per dispatcher (one per process). The dispatcher receives the factory instead of building it, so it constructs no I/O of its own and tests inject a fake or failing factory (`make_runlog` in `dispatcher/tests/helpers/fakes.py`) without patching. Rejected: `Dispatcher.__init__` building the factory from `planner.surface`, which made the link hold by construction but tied the loop to the file writer, so tests had to monkeypatch `RunLog`/`RunLogFactory`; injecting a `surface -> RunLogFactory` callable, which keeps the by-construction link but adds an indirection for no current need; callers building the factory from a separately passed surface with a runtime equality check in the dispatcher, which let the two disagree until startup; and the dispatcher reading the surface from the run-log session, which made the logger the owner of what the model is sent.
- **A step is a `StepRef` plus an optional `StepDispatch`, composed in `StepResult`.** `StepRef` (`call_index`, `plan_step`, `skill`, `params`) is what every recorded step has; `StepDispatch` (`index`, `timeout_s`, `motion_cost`, `fault`) is what the dispatcher decided for a dispatched step. The loop builds both once per dispatched step (the pre-check builds the `StepRef` of a rejection), emits them in `StepStart` and stores them on the `StepResult`, so `step_start` and `step_result` carry the same nested `ref` and `dispatch` objects; a rejected step has `dispatch = null`. Composition keeps the dispatched fields strictly typed (`index` and `timeout_s` are never null) and the step identity built in one place instead of being passed field by field. Rejected: a flat `StepResult` with the identity and dispatch fields at the top level, which made `step_start` take the fields one by one and re-flatten the dispatch object; inheritance `StepResult(StepRef, StepDispatch)`, which would force the dispatch fields optional because rejected steps are also `StepResult`s.
- **`StepResult` holds the skill process as one `ProcessRun`, and dumps it flat.** `ProcessRun` (`response`, `exit_code`, `pid`, `stderr_tail`) is what the executor's `ExecResult` holds, so the loop passes it on whole instead of copying the fields one by one, and a step without a process (rejected, or never started because the service could not be armed) has `process = None` instead of four nulls set by hand. A wrap serializer on `StepResult` writes the four fields at the top level, in their place in the record (`response` after `error_message`, the other three after `duration_ms`), null without a process, so `step_result`, `task_start.previous_task.last_step` and every reader of the log see one fixed key order (`ref`, `dispatch`, `outcome`, `error_code`, `error_message`, `response`, `duration_ms`, `exit_code`, `pid`, `stderr_tail`, `verification`, `faults`); a unit test pins it. `StepResult.response` reads through to the process for the context and the loop. Rejected: a flat `StepResult` with a `from_exec` constructor, which keeps the copy and two definitions of the same four fields; a nested `process` object in the record, which changes the study's record contract.
- **The loop resolves a step's stub fault while building its `StepDispatch`.** It calls `cfg.stub.fault_at(index)` with the task-wide dispatched index; config validation already rejects `stub.faults` under the real backend and keeps one fault per step, so the loop does not re-check the backend. The executor delivers it by class: a robot fault (`ROBOT_FAULTS`) goes in the stub fault file (`contract.faults.write_fault`, parent folder created as for the state file) before the step's `arm`, a skill fault (`SKILL_FAULTS`) in `GO2_STUB_FAULT` for the skill process only; a step without a fault writes nothing ([service.md](../../services/robot/docs/service.md#fault-injection)). The fault belongs to `StepDispatch` because `step_start` records it before the executor runs. Rejected: resolving the fault in the executor, which would need the dispatched index passed in and the fault handed back for the log.
- **Skills and utilities share one stderr tail rule, `stderr_tail` in `dispatcher/execution/process.py`** (the last `STDERR_TAIL_CHARS` characters, null when empty), but read stderr differently. A skill's stderr is drained by reader threads while it runs, trimming the buffer as it reads so memory stays bounded; a utility (`stop_move`, `read_state`) is short-lived and uses `communicate()`, keeping the stop path thread-free and short. Rejected: running utilities through the reader threads, which adds threads to the stop path for no gain.
- **Observers never run on the stop path before the kill.** `request_stop` and the shutdown StopMove are called from the transport thread; they write `stop_requested` / `stop_move` to the plain log under the state lock as before, and call the observer only after `kill_current` (and after StopMove), outside the lock. An observer can block (the console trace writes to a stderr that may be a paused pipe or a terminal stopped with Ctrl+S), and it must never delay the kill or StopMove. So `Dispatcher._log` holds the plain `RunLog`: `request_stop` calls `_log.emit(StopRequested(...))` under the lock and `publish(event, None, observers)` after the kill; the loop thread writes every other record through `publish`. Rejected: calling `publish` from `request_stop`, which ran observer code under the state lock and before the kill.
- **Live output observes the run log events instead of living in the loop.** The task events are already the complete, typed list of what happens in a task, so the observer interface is that list (`Observer.emit`) and `publish` hands each event to the log and to the observers, guarding the observer calls. The loop is unchanged and has no presentation code; transports choose an observer when they build the dispatcher. Rejected: Python `logging` handlers, which flatten the typed payloads (`LLMResult`, `StepResult`, ...) into strings or untyped `extra` dicts; printing inside the loop, which mixes presentation into the loop and would also print in the bot and the tests.
- **Stream events reach the observers through the same `emit`, but never the writer.** `RobotSample` and `RobotLink` are members of `Event` but not of `TaskEvent`, so `RunLog.emit` cannot be handed one (a type error) and the observers need one interface only. The stream writer owns the subscription and its own file, emits them directly to the observers (not through `publish`), guards each call and prints one warning per task, so a blocking or failing observer can at worst delay the stream file, never a record, the loop or the stop path. Rejected: a separate narrow stream-observer interface, which gave each observer two empty methods for events it ignores; stream events in `TaskEvent`, which would make the writer handle events it never writes.
- **One file per task plus an index.** A task is the unit of analysis; the index lets analysis select tasks by condition without parsing every file.
- **One folder per provider and backend, derived from config.** `{log.dir}/{llm.provider}/{robot.backend}/` keeps research data (`anthropic/real`) physically apart from stub and OpenRouter runs, so an analysis that reads one folder cannot mix them, and the logs of the two providers can keep their native shapes ([llm.md](llm.md#design-decisions)). The folder is computed, never configured, so a run cannot be filed under the wrong combination; `provider` and `backend` are still in every index row and in `task_start.config`, so a file stays self-describing if moved. The lock stays at the root so one process at a time writes anywhere under it. Rejected: one shared folder filtered by index columns, which relies on every analysis remembering the filter; a hand-set `log.dir` per combination, which can be forgotten.
- **Every line is flushed as it is written**, under a lock, so a crash leaves a usable partial log and lines from the transport thread never interleave.
- **The exact prompt surface is logged with every task** (system text, catalog, tool schema, registry hash) and the exact user message with every call, so any call can be reconstructed and conditions can be told apart by hash.
- **`kill_to_stop_ms` is measured by the dispatcher, from the kill to the stop it confirms.** It is the number the safety case needs and the only one that covers both stop paths; the service's `stop_call_ms` and the utility's timing explain its parts. Rejected: deriving it from the parts (the IPC wait, a failed service stop and process start are in none of them).
- **The return reason is logged on every call.** Without it, horizon 1 would look like constant replanning; with it, `plan_complete` calls can be separated from replans after a failure ([loop-and-context.md](loop-and-context.md#return-reasons)).
- **Horizon rejections are a `plan_invalid` kind** (`rejection_kind == "horizon"`, with `steps_in_plan` and `horizon`), not a record of their own: one record per invalid reply, and the rate is still a simple filter per condition ([llm.md](llm.md#horizon-rejection)). The two fields are always present (null for other kinds) so every `plan_invalid` has the same keys.
- **One place per StopMove result**: the `stop_move` record. A killed step's `step_result` does not repeat it; the `stop_move` record follows it directly.
- **Infrastructure retries are logged separately and never counted as LLM calls or replans**, so provider trouble does not contaminate the replanning metric.
- **`robot_io` is a field set by the code, not a config key.** The robot service is the only state path, so there is nothing to configure; the field marks the records for analysis, which must not mix them with records written with per-process DDS. Existing logs are not rewritten: a missing field identifies them.
- **The state stream is a separate file per task, not records in the task log.** At 10 Hz it would outnumber every other record many times over and interleave with them from a second thread; a sibling file keeps the task log's record contract and `seq` unchanged and can be skipped by analysis that does not need it. Rejected: one stream file per session (harder to cut per task); the full message rate (five times the lines for no study metric that needs it).
- **Full robot state is logged, not just posture**, as the data for setting and checking the verification thresholds ([loop-and-context.md](loop-and-context.md#verification)).
- **A precondition violation is its own record, not a `step_result`.** The step never ran, has no dispatch index and is no failure; a `step_result` would need a new outcome and would enter the step counts, the context history and the Telegram step list. The record carries the full pre-step state, so the observed posture can be checked against the raw fields. Rejected: putting the state on `task_end`, which mixes a per-step fact into the task summary.
- **`faults` is logged beside `verification`.** The verdict alone cannot tell a fall from a missed goal, and the study needs both rates. Rejected: a verdict per component, which doubles the fields for a split that `faults` already gives.
