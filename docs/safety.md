# Safety

The dispatcher limits what the LLM can make the robot do, and it stops the robot on request, on timeouts and on errors. Still, **the physical/remote e-stop is the final safety measure.** Nothing in software replaces a person holding the remote. Spec: §13, §14, §15, §18.

## Supervised-operation rules

- Run the real robot only under supervision, with the remote (e-stop) in hand and a clear area around the robot.
- Before the first real run on a machine, work through the robot checklist in `docs/testing.md`, in order.
- Keep one dispatcher per robot. The lock prevents a second process on the same `log.dir`, but not one on another machine or with another `log.dir`.
- After a `sit`, stand the robot up with the remote. There is no `stand` skill (OD-1).
- If an outcome ends with `WARNING: the stop command to the robot failed. Stop the robot manually.`, use the remote at once.
- Do not raise `motion_budget.*`, the walk/turn ranges in `SKILL.md`, or the policy timeouts without a reason recorded in `docs/decisions.md`.

## The stop path

All stops use one path: **kill the running skill process, then send `StopMove` from a fresh process.**

| Trigger | How |
|---|---|
| Operator `stop` | Telegram `stop` (any case, trimmed) or `/stop`; first Ctrl+C in the CLI. `request_stop()` sets the stop event and kills the current skill. It returns at once, from any thread. |
| Step timeout | The executor kills the step when it runs longer than `POLICY.timeout_s(params)`. The outcome is `timeout` (a failure), and the task goes on with a replan. |
| Task time limit | The executor kills the step when `loop.task_time_limit_s` runs out. The outcome is `TIME_LIMIT_EXCEEDED`. |
| Shutdown | A second Ctrl+C, SIGTERM, a Telegram bot shutdown, or `atexit`: `Dispatcher.shutdown()`. In `go2-bot`, Ctrl+C or SIGTERM first calls `request_stop("shutdown")`, so a running task is stopped at once (kill + StopMove, outcome `STOPPED`) before the Telegram library shuts down; `shutdown()` remains the backstop. |
| Internal error | An unhandled exception in the dispatcher: kill + StopMove, outcome `INTERNAL_ERROR`. Logging, kill and StopMove are each guarded, so `task_end` and the index line are always written. |

Details:

- Each skill runs in its own session. The kill is `SIGKILL` to the whole process group. The executor checks the process exit and sends the kill under the same lock, so a reused PID is never signalled.
- `StopMove` runs as `python -m go2_skills.stop_move` in a new process. A fresh process gets a clean DDS channel (the SDK uses a process-wide singleton). It is never registered as the "current" process, so a stop can never kill it. It waits up to `robot.stop_move_timeout_s` (10 s). It is not retried. `Executor.stop_move()` never raises: if the process cannot even be started, it returns a failed result (error text in `stderr_tail`), so the operator still gets the StopMove warning.
- If a stop or the time limit arrives while **no** step runs (during an LLM call or between steps), the dispatcher still sends StopMove when the task ends. This is idempotent.
- StopMove is not sent after a step that ends normally: walk and turn already end with `StopMove()`.
- An HTTP request to the LLM that is already in progress is not cancelled. The stop takes effect when it returns. Stop and deadline are checked before each attempt and during retry backoff.

## Measured latency

`stop_move` records `timing.stop_call_ms`: the time from the start of the utility process to the return of `StopMove()`. This covers interpreter start, DDS init and the call. Total kill-to-stop latency = executor kill + process start + `stop_call_ms`. The robot checklist (item 5) measures it on the real robot, and the result is recorded in `docs/decisions.md`. The spec does not set a required number. Every StopMove result is in the run log (`stop_move` records, with `duration_ms` and the robot state 0.5 s after the call).

## Between kill and StopMove

A SIGKILL cannot run cleanup code, so the killed skill cannot send `StopMove()` itself. Until `StopMove` arrives (process start + DDS init, see above), the robot has received no stop command. It has not been checked whether the Go2 sport service stops on its own when `Move` commands stop arriving. Until the checklist has measured this, assume the robot may keep moving at the last commanded velocity (≤ 0.3 m/s, or ≤ 1 rad/s when turning) for that time.

## If the dispatcher dies

Each skill process watches its parent PID (`GO2_PARENT_PID`) every 0.2 s. If the dispatcher disappears, motion loops break and send `StopMove()` themselves. Any skill still alive 2 s later exits (code 137). `stop_move` and `read_state` do not watch their parent, so a StopMove always finishes. Every normal exit of the transports calls `Dispatcher.shutdown()`.

If the dispatcher host loses power or the network drops, none of this helps. Use the e-stop.

## Motion budget

Each task may command at most `motion_budget.max_distance_m` (10 m) of travel and `max_rotation_deg` (720°) of rotation. Before a plan runs, its steps up to `stop_at` are simulated against what the task has used so far. A plan that would go over the limit is rejected **before any step runs**, so no plan stops partway because of a later step.

The budget counts **commanded** motion, not measured motion. It is charged when a step is dispatched, even if the step then fails or is killed. Walk and turn are open-loop (velocity × time), so the actual distance can differ.

## Bounds

The dispatcher checks every step of a plan against its `SKILL.md` declaration before anything runs: walk 0.1–3.0 m, turn 5–180°, enum values, required params, no unknown skills or params. A violation rejects the whole plan (one failure, then a replan). Non-finite or overflowing numbers (e.g. `1e400` as an int) are type violations. Skills check only presence and types and apply no defaults, so a skill run by hand is not range-limited, and a missing parameter (even one with a default in `SKILL.md`) is `invalid_params`.

## StopMove failure

If the `stop_move` process cannot be started, times out, exits without a valid response, or reports a non-zero SDK code, `stop_move_failed` is set on the task. The operator message then ends with the warning above. The `stop_move` record holds the exit code, stderr tail and response. There is no automatic retry. A person must stop the robot.

## Single-instance lock

`run`, `batch`, `--reset-stub` and `go2-bot` hold an exclusive `flock` on `{log.dir}/.dispatcher.lock`. A second one exits with code 2 (`Another dispatcher is running`). `catalog` and `state` do not take the lock; `state` only reads.

## The robot is stationary during LLM calls

Steps run only between LLM calls, never during one. When the dispatcher calls the model, the previous step has finished (its motion loop ended with `StopMove()`) or has been killed and stopped. Slow or failing LLM calls therefore never leave the robot moving.

## What the LLM cannot do

- Run anything that is not a declared skill with declared params in range.
- See or invoke `stop_move` or `read_state`.
- Exceed the motion budget, the horizon, the call budget or the time limit.
- Receive raw stderr, tracebacks or provider error text. Every problem reaches it as one short shaped line.
