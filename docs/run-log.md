# Run log

Every task writes a complete JSONL log. It is the dataset for the study: tokens, latency and replanning can all be measured from it. Spec: §17.

## Files

- One file per task: `{log.dir}/{YYYYMMDDTHHMMSS}_{run_id[:8]}.jsonl`, using the local time at task start. Example: `runs/20261001T113603_b6bb52fe.jsonl`.
- `{log.dir}/index.jsonl`: one line per finished task.
- `{log.dir}/.dispatcher.lock`: the single-instance lock (not a log).
- `{log.dir}/.stub_state.json`: the stub state, at the default `stub.state_file` (not a log).

`run_id` is a uuid4 hex per task. `session_id` is a uuid4 hex per process (one CLI invocation, one bot run); it links the tasks of one session.

Each line is written and flushed while a lock is held, so a crash leaves a usable partial log, and the `stop_requested` record written from the transport thread cannot interleave with other lines.

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

Examples below are from a stub run with a scripted planner (long fields shortened with `…`).

### `task_start`

`task`, `source` (`cli` / `telegram` / `test`), `sender_id` (Telegram user id or null), `condition`, `config` (full config dump; it holds no secrets), `registry_hash`, `system_text`, `catalog_text`, `tool_schema`, `skills` (names), `previous_task` (`TaskSummary` or null), `posture`, `versions` (`python`, `anthropic`, `pydantic`, `go2_dispatcher`), `git_commit` (`git rev-parse HEAD` in the base dir, or null).

```json
{"ts":"2026-10-01T11:36:03.637094+03:00","t_mono_ms":0.44,"session_id":"8877711a…","run_id":"b6bb52fe…","seq":0,"type":"task_start","task":"turn left 90 degrees, then tell me if you see a chair","source":"cli","sender_id":null,"condition":"","config":{"run":{"condition":""},"llm":{"model":"claude-sonnet-5-5",…},…},"registry_hash":"bf06b5af6abd480b","system_text":"You plan actions for a Unitree Go2 …","catalog_text":"detect_object: …","tool_schema":{"name":"submit_plan",…},"skills":["detect_object","sit","stretch","turn","walk"],"previous_task":null,"posture":"standing","versions":{"python":"3.10.12","anthropic":"1.11.0","pydantic":"2.13.5","go2_dispatcher":"0.1.0"},"git_commit":null}
```

### `llm_request`

`call_index` (1-based), `return_reason` (`initial` / `plan_complete` / `checkpoint` / `failure` / `schema_retry`), `retry_of` (for `schema_retry`: the reason being retried; otherwise null), `user_text` (the exact user message).

```json
{…,"seq":1,"type":"llm_request","call_index":1,"return_reason":"initial","retry_of":null,"user_text":"## Previous task\n(none)\n\n## Robot\nPosture: standing\n\n## Task\nturn left 90 degrees, then tell me if you see a chair\n\n## Budget\n…"}
```

### `llm_retry`

An infra retry (not an LLM call): `call_index`, `attempt` (1-based number of the attempt that failed), `error_type`, `status_code` (null for connection errors), `attempt_latency_ms`, `sleep_s`.

```json
{…,"type":"llm_retry","call_index":2,"attempt":1,"error_type":"InternalServerError","status_code":529,"attempt_latency_ms":812.4,"sleep_s":1.0}
```

### `llm_response`

`call_index`, `latency_ms` (successful attempt only), `total_ms` (whole call including failed attempts and backoff), `attempts`, `stop_reason`, `usage` (verbatim from the API), `content` (all response blocks), `response_id`, `request_id`.

```json
{…,"seq":2,"type":"llm_response","call_index":1,"latency_ms":1834.2,"total_ms":1834.9,"attempts":1,"stop_reason":"tool_use","usage":{"input_tokens":100,"output_tokens":20},"content":[{"type":"tool_use","id":"toolu_…","name":"submit_plan","input":{"status":"PLAN","steps":[…]}}],"response_id":"msg_…","request_id":"req_…"}
```

### `llm_error` / `llm_interrupted`

`llm_error`: `call_index`, `detail` (for example `"BadRequestError 400"`). The task ends `LLM_ERROR`.
`llm_interrupted`: `call_index`, `cause` (`operator` / `task_time_limit`). A stop or the deadline cut the retries short.

```json
{…,"type":"llm_error","call_index":1,"detail":"BadRequestError 400"}
```

### `plan`

A valid plan: `call_index`, `plan` (`status`, `steps`, `replan_after`, `message`), `stop_at` (`replan_after` or the number of steps).

```json
{…,"seq":3,"type":"plan","call_index":1,"plan":{"status":"PLAN","steps":[{"skill":"turn","params":{"direction":"left","angle_deg":90}},{"skill":"detect_object","params":{"target":"chair"}}],"replan_after":null,"message":null},"stop_at":2}
```

### `horizon_rejection` / `plan_invalid`

An invalid reply. `horizon_rejection` (more steps than the horizon): `call_index`, `tool_input`, `steps_in_plan`, `horizon`, `errors` (all errors, horizon first). `plan_invalid` (any other invalid reply): `call_index`, `tool_input`, `rejection_kind` (`schema` / `semantic` / `no_tool_call` / `max_tokens`), `errors`.

```json
{…,"type":"plan_invalid","call_index":1,"tool_input":{"status":"DONE","steps":[]},"rejection_kind":"semantic","errors":["status DONE needs a message"]}
```

### `step_start`

`index` (task-wide dispatched count), `call_index`, `plan_step`, `skill`, `params` (filled), `timeout_s`, `motion_cost`, `fault`.

```json
{…,"seq":4,"type":"step_start","index":1,"call_index":1,"plan_step":1,"skill":"turn","params":{"direction":"left","angle_deg":90},"timeout_s":12.356,"motion_cost":{"distance_m":0.0,"rotation_deg":90.0},"fault":null}
```

### `step_result`

All `StepResult` fields at the top level: `index` (null if not dispatched), `call_index`, `plan_step`, `skill`, `params`, `outcome`, `error_code`, `error_message`, `response` (full `SkillResponse` with `state_before` / `state_after` / `timing`), `duration_ms`, `timeout_s`, `motion_cost`, `fault`, `exit_code`, `pid`, `stderr_tail`, `stop_move`, `verification` (always `"unverified"`). It also adds `budget_used`, `failures` and `posture` **after** this step has been counted. It is written for both dispatched and rejected steps.

```json
{…,"seq":5,"type":"step_result","index":1,"call_index":1,"plan_step":1,"skill":"turn","params":{"direction":"left","angle_deg":90},"outcome":"ok","error_code":null,"error_message":null,"response":{"schema_version":1,"skill":"turn","status":"ok","observations":{"direction":"left","angle_deg":90.0,"duration_s":1.6,"sdk_ret":0},"error":null,"state_before":{…},"state_after":{…},"state_error":null,"timing":{"init_ms":0.007,"exec_ms":22.999,"state_ms":5.272,"total_ms":28.342}},"duration_ms":101.87,"timeout_s":12.356,"motion_cost":{"distance_m":0.0,"rotation_deg":90.0},"fault":null,"exit_code":0,"pid":505362,"stderr_tail":null,"stop_move":null,"verification":"unverified","budget_used":{"distance_m":0.0,"rotation_deg":90.0},"failures":0,"posture":"standing"}
```

### `stop_requested`

`source` (`cli` / `telegram` / `shutdown`), `during` (`llm_call` / `step` / `between`).

```json
{…,"type":"stop_requested","source":"telegram","during":"step"}
```

### `stop_move`

All `StopMoveResult` fields: `ok`, `reason` (`operator` / `task_time_limit` / `step_timeout` / `shutdown` / `internal_error`), `duration_ms`, `exit_code`, `response` (its `timing.stop_call_ms` and `state_after`), `stderr_tail`. It is written after the `step_result` of a killed step, and for every StopMove the dispatcher sends itself.

```json
{…,"type":"stop_move","ok":true,"reason":"operator","duration_ms":612.3,"exit_code":0,"response":{"schema_version":1,"skill":"stop_move","status":"ok","observations":{"sdk_ret":0},…,"timing":{"stop_call_ms":48.1,"state_ms":5.2,"total_ms":560.4}},"stderr_tail":null}
```

### `exception`

`where`, `exception_type`, `message`, `traceback`. The task ends `INTERNAL_ERROR`. The exception class is stored as `exception_type`, not `type`, because `type` is the record type (see `docs/decisions.md`, T9).

### `task_end`

`outcome`, `message`, `llm_calls`, `failures`, `steps_recorded`, `steps_dispatched`, `rejections` (non-dispatched steps), `horizon_rejections`, `usage_totals` (sum of every top-level numeric `usage` field over all responses), `budget_used`, `stop_move_failed`, `final_posture`, `duration_ms`.

```json
{…,"seq":11,"type":"task_end","outcome":"DONE","message":"I turned left 90 degrees and see a chair ahead, close by.","llm_calls":2,"failures":0,"steps_recorded":2,"steps_dispatched":2,"rejections":0,"horizon_rejections":0,"usage_totals":{"input_tokens":200,"output_tokens":40},"budget_used":{"distance_m":0.0,"rotation_deg":90.0},"stop_move_failed":false,"final_posture":"standing","duration_ms":156.1}
```

## `index.jsonl`

One line per finished task, without the envelope:

```json
{"run_id":"b6bb52fe55bb41589a5a1fc51c463f72","file":"20261001T113603_b6bb52fe.jsonl","ts_start":"2026-10-01T11:36:03.636693+03:00","task":"turn left 90 degrees, then tell me if you see a chair","source":"cli","outcome":"DONE","condition":"","backend":"stub","planning_horizon":5,"max_llm_calls":20,"registry_hash":"bf06b5af6abd480b","llm_calls":2,"failures":0,"steps_dispatched":2,"input_tokens":200,"output_tokens":40,"duration_ms":156.1}
```

`file` is relative to `log.dir`. A task whose log could not be opened has no index line.

## Computing the study metrics

| Metric | From |
|---|---|
| Tokens per task | `index.jsonl` `input_tokens` / `output_tokens`, or `task_end.usage_totals` (includes cache fields if any) |
| Tokens per call | `llm_response.usage` |
| LLM latency per call | `llm_response.latency_ms` (successful attempt) and `total_ms` (with retries and backoff); `llm_retry.attempt_latency_ms` |
| Calls by return reason | count `llm_request` by `return_reason` |
| Failures, rejections | `task_end.failures`, `task_end.rejections`; `step_result.outcome` |
| Horizon rejection rate | `horizon_rejection` records ÷ `llm_response` records |
| Skill wall time | `step_result.duration_ms` |
| Process overhead | `step_result.duration_ms − response.timing.total_ms` |
| Stop latency | `stop_move.response.timing.stop_call_ms`, `stop_move.duration_ms` |
| Exact prompts | `task_start.system_text` / `catalog_text` / `tool_schema` + `llm_request.user_text` |
| Robot state | `step_result.response.state_before` / `state_after`; `stop_move.response.state_after` |
| Condition | `condition`, `registry_hash`, `planning_horizon`, `max_llm_calls` (index and `task_start`) |

## Analysis example

```python
import json
from collections import Counter
from pathlib import Path

log_dir = Path("runs")
index = [json.loads(l) for l in (log_dir / "index.jsonl").open()]

by_cond: dict[str, list[dict]] = {}
for row in index:
    by_cond.setdefault(f'{row["condition"]}/h{row["planning_horizon"]}', []).append(row)

for cond, rows in sorted(by_cond.items()):
    n = len(rows)
    done = sum(r["outcome"] == "DONE" for r in rows)
    reasons, horizon_rej, responses, overhead = Counter(), 0, 0, []
    for r in rows:
        for line in (log_dir / r["file"]).open():
            rec = json.loads(line)
            t = rec["type"]
            if t == "llm_request":
                reasons[rec["return_reason"]] += 1
            elif t == "llm_response":
                responses += 1
            elif t == "horizon_rejection":
                horizon_rej += 1
            elif t == "step_result" and rec["index"] is not None and rec["response"]:
                overhead.append(rec["duration_ms"] - rec["response"]["timing"]["total_ms"])
    print(f"{cond}: {n} tasks, {done} DONE, "
          f"mean in/out tokens {sum(r['input_tokens'] for r in rows)/n:.0f}/"
          f"{sum(r['output_tokens'] for r in rows)/n:.0f}, "
          f"mean calls {sum(r['llm_calls'] for r in rows)/n:.2f} {dict(reasons)}, "
          f"horizon rejections {horizon_rej}/{responses}, "
          f"mean process overhead {sum(overhead)/max(len(overhead),1):.0f} ms")
```
