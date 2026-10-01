# Safety

Everything that limits or stops the robot. The dispatcher bounds what the LLM can make the robot do and stops it on request, on timeouts and on errors. Still, **the physical/remote e-stop is the final safety measure.** Nothing in software replaces a person holding the remote.

## Supervised-operation rules

- Run the real robot only under supervision, with the remote (e-stop) in hand and a clear area around the robot.
- Before the first real run on a machine, work through the robot checklist in [robot.md](robot.md), in order. The real backend has never been run.
- Keep one dispatcher per robot. The lock ([below](#single-instance-lock)) prevents a second process on the same `log.dir`, not one on another machine or with another `log.dir`.
- After a `sit`, stand the robot up with the remote: no skill can stand it up ([robot.md](robot.md)).
- If an outcome ends with `WARNING: the stop command to the robot failed. Stop the robot manually.`, use the remote at once.
- Do not raise `motion_budget.*`, the walk/turn ranges in `SKILL.md`, or the policy timeouts without recording the reason in the owning document.

## The stop path

All stops use one path: **kill the running skill process group, then send `StopMove` from a fresh process.**

| Trigger | How |
|---|---|
| Operator stop | Telegram: the stop word or `/stop`. CLI: the first Ctrl+C. `request_stop()` sets the stop event and kills the current skill; it returns at once and is safe from any thread. While idle the bot replies `Nothing is running.` |
| Step timeout | The executor kills a step that runs longer than `POLICY.timeout_s(params)` ([skills.md](skills.md#policies)). Outcome `timeout`, a failure; the task continues with a replan. |
| Task time limit | `loop.task_time_limit_s` (300 s, *tunable*) from task start. Checked before every LLM call and step, during LLM retry backoff, and by the executor while a step runs. Outcome `TIME_LIMIT_EXCEEDED`. |
| Shutdown | A second Ctrl+C, SIGTERM, Telegram bot shutdown, or `atexit`: `Dispatcher.shutdown()`. In `go2-bot`, SIGINT/SIGTERM first calls `request_stop("shutdown")`, so a running task is stopped at once (outcome `STOPPED`) before the Telegram library shuts down; `shutdown()` remains the backstop and, if the task is still running after its wait, kills it and sends `StopMove` itself. |
| Internal error | An unhandled exception in the dispatcher: kill + `StopMove`, outcome `INTERNAL_ERROR`. Logging, kill and `StopMove` are each guarded, so `task_end` and the index line are always written. |

**Stop word.** The Telegram message `stop` in any case, with surrounding spaces trimmed (`text.strip().lower() == "stop"`), or the `/stop` command. It is checked before the busy check, so it gets through while a task runs.

Details:

- Each skill runs in its own session; the kill is `SIGKILL` to the whole process group. The executor checks for process exit and sends the kill under one lock, so a reused PID is never signalled. The first kill cause wins.
- `StopMove` runs as `python -m go2_skills.stop_move '{}'` in a new process. A fresh process gets a clean DDS channel (the SDK uses a process-wide singleton). It is never registered as the current process, so a stop can never kill it. It waits up to `robot.stop_move_timeout_s` (10 s) and is not retried. It sends `StopMove` even if its params are invalid, waits 0.5 s and samples the state. `Executor.stop_move()` never raises: if the process cannot even be started, it returns a failed result, so the operator still gets the warning.
- If a stop, the time limit or shutdown kills a running step, the executor sends `StopMove` right after the kill, and that is the only one: the task then ends without a second, end-of-task `StopMove`. How the posture is updated: [loop-and-context.md](loop-and-context.md#user-message).
- If a stop or the time limit arrives while **no** step runs (during an LLM call or between steps), the dispatcher still sends `StopMove` when the task ends. This is idempotent.
- `StopMove` is not sent after a step that ends normally: walk and turn already end with `StopMove()`.
- An LLM request already in flight is not cancelled; the stop takes effect when it returns ([llm.md](llm.md#infrastructure-retries)).

### StopMove failure

If the `stop_move` process cannot be started, times out, exits without a valid response, or reports a non-zero SDK code, `stop_move_failed` is set on the task and the operator message ends with the warning above. The `stop_move` record in the run log holds the exit code, stderr tail and response. There is no automatic retry; a person must stop the robot.

### Between kill and StopMove

A SIGKILL runs no cleanup, so the killed skill cannot send `StopMove()` itself. Until the new `StopMove` arrives (process start + DDS init), the robot has received no stop command. Whether the Go2 sport service stops on its own when `Move` commands stop arriving is **unverified on the robot**. Until the checklist has measured it, assume the robot may keep moving at the last commanded velocity (≤ 0.3 m/s, or ≤ 1 rad/s when turning) for that time.

### Measured latency

`stop_move` records `timing.stop_call_ms`: from the start of the utility process to the return of `StopMove()` (interpreter start, DDS init and the call). Kill-to-stop latency = executor kill + process start + `stop_call_ms`. There is no required number; the real value is **unverified on the robot** and is measured by the robot checklist ([robot.md](robot.md)). Every `StopMove` is logged as a `stop_move` record with `duration_ms` and the state 0.5 s after the call ([run-log.md](run-log.md)).

## Busy-reject

One task runs at a time. While it runs, any other message gets `Busy: a task is running. Send "stop" to stop it.` There is no queue and no interrupt by a new task; only the stop word gets through. The system assumes a single operator. The Telegram bot handles updates concurrently, because otherwise `stop` could not arrive during a task ([running.md](running.md)).

## The robot is stationary during LLM calls

Steps run only between LLM calls, never during one. When the dispatcher calls the model, the previous step has finished (its motion loop ended with `StopMove()`) or has been killed and stopped. A slow or failing LLM call never leaves the robot moving.

## Motion budget

Each task may command at most `motion_budget.max_distance_m` (10 m) of travel and `motion_budget.max_rotation_deg` (720°) of rotation (both *tunable*). Walk costs its distance, turn its angle, other skills nothing ([skills.md](skills.md#policies)).

- The budget bounds **commanded** motion, not measured motion. Walk and turn are open-loop (velocity × time), so the real distance can differ.
- It is charged when a step is **dispatched**, even if the step then fails or is killed, because commanded motion may already have happened. Steps rejected before running are not charged.
- A plan whose steps up to its checkpoint would go over the limit is rejected **before any step runs**, as one failure. The mechanics are in [loop-and-context.md](loop-and-context.md#whole-plan-pre-check).
- Usage is shown to the model on every call (`Travel: 2 of 10 m used`).

## Bounds

Every step of a plan is checked against its `SKILL.md` declaration before anything runs: walk 0.1–3.0 m, turn 5–180°, enum values, required params, no unknown skills or params. A violation rejects the whole plan (one failure, then a replan). Details in [loop-and-context.md](loop-and-context.md#step-bounds). Skills check only presence and types, so a skill run by hand is not range-limited.

## If the dispatcher dies

Each skill process watches its parent (`GO2_PARENT_PID`) every 0.2 s. If the dispatcher disappears, the `ORPHANED` flag is set; motion loops check it on every iteration, break, and send `StopMove()` themselves (the step reports `orphaned: true`). Any skill still alive 2 s later exits with code 137. The watchdog does nothing when `GO2_PARENT_PID` is unset (manual runs). `stop_move` and `read_state` do not watch their parent, so a `StopMove` always finishes. Every normal exit of the transports calls `Dispatcher.shutdown()`.

If the dispatcher host loses power or the network drops, none of this helps. Use the e-stop.

## Single-instance lock

`run`, `batch`, `--reset-stub` and `go2-bot` take an exclusive, non-blocking `flock` on `{log.dir}/.dispatcher.lock` and hold it until they exit. A second one prints `Another dispatcher is running (lock: <path>).` and exits 2. `catalog` and `state` do not take the lock; `state` only reads.

## What the LLM cannot do

- Run anything that is not a declared skill with declared params in range.
- See or invoke `stop_move` or `read_state`.
- Exceed the motion budget, the horizon, the call budget or the task time limit.
- Receive raw stderr, tracebacks or provider error text. Every problem reaches it as one short line.

## Design decisions

- **One stop path** (kill the process group, then `StopMove` from a fresh process) for operator stop, step timeouts, the task time limit, shutdown and internal errors. One tested mechanism; a killed process cannot clean up after itself, and a fresh process has a clean DDS channel.
- **The stop word bypasses busy-reject**, because a stop must reach a running task. Accepted variants: any case, surrounding spaces trimmed, and Telegram `/stop`.
- **Busy-reject: no queue, no interrupt by new tasks.** It keeps one task, one plan and one log at a time; the system assumes a single operator. Forwarding mid-run messages to the model is a future idea ([roadmap.md](roadmap.md#future-ideas)).
- **A per-task cumulative motion budget, separate for travel and rotation.** Per-step bounds alone would let ten 3 m walks through. It bounds commanded motion because v1 has no trusted position measurement; estimating position from commanded velocity (dead reckoning) was rejected, because it restates the walk skill's own open-loop assumption. A geofence waits for a real position source ([roadmap.md](roadmap.md#verifiability-per-skill)).
- **Charge the budget at dispatch, however the step ends**, because commanded motion may have happened even if the step errors or is killed.
- **Orphan watchdog in skill processes**, so skills stop the robot and exit if the dispatcher dies.
- **One dispatcher process per machine (file lock)**, so two processes can never drive one robot, overwrite the stub state, or interleave `index.jsonl`.
- **The robot is stationary during LLM calls**, so a slow or failing LLM call never leaves the robot moving.
- **The physical/remote e-stop remains the final safety measure.** The software stops are best effort; their real latency is still to be measured.
