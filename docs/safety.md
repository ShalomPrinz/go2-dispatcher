# Safety

Everything that limits or stops the robot. The dispatcher bounds what the LLM can make the robot do and stops it on request, on timeouts and on errors. Still, **the physical/remote e-stop is the final safety measure.** Nothing in software replaces a person holding the remote.

## Supervised-operation rules

- Run the real robot only under supervision, with the remote (e-stop) in hand and a clear area around the robot. This holds for both planner providers: `llm.provider = "openrouter"` with `robot.backend = "real"` is allowed and follows exactly these rules; a free or untested model is no reason to relax them, since every safety limit below is enforced by the dispatcher whatever the model plans.
- Before the first real run on a machine, work through the robot checklist in [robot.md](../skills/docs/robot.md), in order. The real backend has run on the robot under supervision with the normal CLI; checklist items 1 (standing posture; sitting not yet measured) and 5 (stop latency, from successful stops only) are partly measured, and the other items are still open ([roadmap.md](roadmap.md#pending-human-work)).
- Keep one dispatcher per robot. The lock ([below](#single-instance-lock)) prevents a second process on the same `log.dir`, not one on another machine or with another `log.dir`.
- When the system runs on the computer mounted on the robot, keep a physical stop within reach at all times: the robot's own remote or a separate stop button. The dispatcher's stop arrives over Wi-Fi; if the Wi-Fi drops, `stop` no longer reaches it.
- After a `sit`, stand the robot up with the remote: no skill can stand it up ([robot.md](../skills/docs/robot.md)).
- If an outcome ends with `WARNING: the stop command to the robot failed. Stop the robot manually.`, use the remote at once.
- Do not raise `motion_budget.*`, the walk/turn ranges in `SKILL.md`, or the policy timeouts without recording the reason in the owning document.
- Coding agents never run the real backend or robot tests. `.claude/settings.json` denies Claude Code commands containing `--backend real` or `--run-robot`, denies reading `.env`, and asks before `--run-live`. It also asks before editing the fixed texts (`dispatcher/prompts.py`, catalog `SKILL.md` files), since that changes the registry hash ([skills.md](../skills/docs/skills.md)).

## The stop path

All stops use one path: **kill the running skill process group and send `stop` to the [robot service](../skills/docs/skills.md#robot-service) on the dispatcher's control connection; if no `stop_sent` arrives within 500 ms (*tunable*), SIGKILL the service and send `StopMove` from a fresh process (the fallback).**

| Trigger | How |
|---|---|
| Operator stop | Telegram: the stop word or `/stop`. CLI: the first Ctrl+C. `request_stop()` sets the stop event and kills the current skill; it returns at once and is safe from any thread. While idle the bot replies `Nothing is running.` |
| Step timeout | The executor kills a step that runs longer than `POLICY.timeout_s(params)` ([skills.md](../skills/docs/skills.md#policies)). Outcome `timeout`, a failure; the task continues with a replan if the stop is confirmed, and otherwise ends `STOP_FAILED` ([StopMove failure](#stopmove-failure)). |
| Task time limit | `loop.task_time_limit_s` (300 s, *tunable*) from task start. Checked before every LLM call and step, during LLM retry backoff, and by the executor while a step runs. Outcome `TIME_LIMIT_EXCEEDED`. |
| Shutdown | A second Ctrl+C, SIGTERM, Telegram bot shutdown, or `atexit`: `Dispatcher.shutdown()`. In `go2 bot`, SIGINT/SIGTERM first calls `request_stop("shutdown")`, so a running task is stopped at once (outcome `STOPPED`) before the Telegram library shuts down; `shutdown()` remains the backstop and, if the task is still running after its wait, kills it and sends `StopMove` itself. |
| Internal error | An unhandled exception in the dispatcher: kill + `StopMove`, outcome `INTERNAL_ERROR`. Logging, kill and `StopMove` are each guarded, so `task_end` and the index line are always written. |
| Robot service lost | The service misses a `ping` (every 0.5 s while a step runs) or a `stop_sent` within 500 ms, or its control connection, an `arm` or a `disarm` fails: SIGKILL the service and the skill process group, then the fallback `StopMove`. See [robot service lost](#robot-service-lost). |

**Stop word.** The Telegram message `stop` in any case, with surrounding spaces trimmed (`text.strip().lower() == "stop"`), or the `/stop` command. It is checked before the busy check, so it gets through while a task runs.

Details:

- Each skill runs in its own session; the kill is `SIGKILL` to the whole process group. The executor checks for process exit and sends the kill under one lock, so a reused PID is never signalled. The first kill cause wins.
- **Leases.** The dispatcher holds the service's one control connection from startup. Before each step it `arm`s the service with a random token and the step timeout as the lease deadline, and passes the token to the skill in `GO2_LEASE_TOKEN`; it `disarm`s when the step ends. Without a lease the service refuses every motion request, which enforces [stationary during LLM calls](#the-robot-is-stationary-during-llm-calls) in the service too. When the skill's connection closes (the kill), the service ends the lease and sends `StopMove` itself, which backs up the dispatcher's `stop` ([skills.md](../skills/docs/skills.md#robot-service)).
- **Service stop.** `stop` makes the service send `StopMove` through its long-lived sport client and end any lease; it replies `stop_sent` with the SDK code and `stop_call_ms`. The executor sends it right after the `SIGKILL`, which does not wait for the process to exit, so the two run in parallel. A `stop_sent` with a non-zero code runs the fallback too, but keeps the service. So does a `disarm` reply whose `stop_failed` reports that a `StopMove` the service sent on its own failed ([loop-and-context.md](../dispatcher/docs/loop-and-context.md#stop-failed)).
- **Fallback.** No `stop_sent` within 500 ms, or a service that is not running or not reachable, counts as a lost service: the executor closes the control connection, SIGKILLs the service and runs the fallback. The fallback is permanent, not a migration aid.
- The fallback `StopMove` runs as `python -m skills.stop_move '{}'` in a new process. A fresh process gets a clean DDS channel (the SDK uses a process-wide singleton). Like every skill, it needs the Ethernet link to the robot: with the link down, no software stop reaches the robot and the physical e-stop is the stop. In the first supervised runs an unstable cable made 5 of 8 `stop_move` calls fail at channel initialisation, so they never sent `StopMove` ([known issues](../skills/docs/robot.md#known-issues)). It is never registered as the current process, so a stop can never kill it. It waits up to its `POLICY` timeout (12 s, [skills.md](../skills/docs/skills.md)) and is not retried. It sends `StopMove` even if its params are invalid, then waits ([settle waits](../skills/docs/robot.md#settle-waits)) and samples the state. `Executor.stop_move()` never raises: if the process cannot even be started, it returns a failed result, so the operator still gets the warning.
- If a stop, the time limit or shutdown kills a running step, the executor sends the stop right after the kill, and that is the only one: the task then ends without a second, end-of-task `StopMove`. How the posture is updated: [loop-and-context.md](../dispatcher/docs/loop-and-context.md#user-message).
- If a stop or the time limit arrives while **no** step runs (during an LLM call or between steps), the dispatcher still sends `StopMove` when the task ends. This is idempotent.
- If the task-start state read through the robot service fails, the task ends `STATE_UNAVAILABLE` and the dispatcher sends `StopMove` (reason `state_unavailable`): with the state unknown it cannot confirm the robot is stationary ([loop-and-context.md](../dispatcher/docs/loop-and-context.md#design-decisions)). A stop that arrived during the read is covered by the same `StopMove`.
- `StopMove` is not sent after a step that ends normally: walk and turn already end with `StopMove()`, and the `disarm` sends one if the lease was used for motion.
- The [vision service](running.md#vision-service) is not part of the stop path: it only reads the camera and never commands motion, so a stop, a step timeout or the time limit kills only the `detect_object` skill process group, as for any skill. A stop that arrives while the executor waits for the service to become ready ends the wait at once; the step is then killed and `StopMove` sent as usual. The service is stopped only when the dispatcher shuts down.
- An LLM request already in flight is not cancelled; the stop takes effect when it returns ([llm.md](../dispatcher/docs/llm.md#infrastructure-retries)).

### Robot service lost

A service that crashes (EOF on the control connection) or hangs (no `pong` or `stop_sent` within 500 ms) mid-task is handled in this order:

1. **Detect** it (above).
2. **Stop first:** SIGKILL the service and the skill process group, then run the fallback `stop_move`.
3. **End the task** `ROBOT_SERVICE_LOST`, with no further LLM call. The operator message says whether the fallback stop was confirmed, and a failed one adds the [StopMove warning](#stopmove-failure).

The service stays down until the next task's fresh state read starts it; a failed start ends that next task `STATE_UNAVAILABLE` ([loop-and-context.md](../dispatcher/docs/loop-and-context.md#robot-service-lost)). `go2 batch` stops at the first task that ends `ROBOT_SERVICE_LOST` ([running.md](running.md#output)). A loss while a stop, the time limit or shutdown is already ending the task only changes which path stops the robot.

Residual risk: between the crash and the fallback `StopMove` (detection up to about 1 s, plus the fallback's process start and DDS init) the robot has no stop command. The system assumes the worst case, that the Go2 keeps its last velocity until a `StopMove` arrives ([robot.md](../skills/docs/robot.md#working-assumptions)).

### StopMove failure

If the service stop fails and the fallback `stop_move` process cannot be started, times out, exits without a valid response, or reports a non-zero SDK code, `stop_move_failed` is set on the task and the operator message ends with the warning above. The `stop_move` record holds the details ([run-log.md](../dispatcher/docs/run-log.md#stop_move)). There is no automatic retry; a person must stop the robot.

A failed stop ends the task with no further LLM call within that task. A step whose stop failed while the service stays alive ends the task `STOP_FAILED`: a killed step, such as a step timeout, or a step after which the service reported a failed `StopMove` of its own and the fallback failed too. A step that a stop, the time limit or shutdown interrupted keeps its outcome, and a lost service keeps `ROBOT_SERVICE_LOST`; both carry the warning. `go2 batch` stops after any task whose stop failed, whatever its outcome ([running.md](running.md#output)).

A later task is not gated: the next Telegram message or a new `go2 run` starts a task and calls the model as usual. The warning on the final message is the only safeguard across tasks; the operator stops the robot before sending the next task.

### Between kill and StopMove

A SIGKILL runs no cleanup, so the killed skill cannot send `StopMove()` itself. The service sends it on the dispatcher's `stop` and, independently, when the skill's connection closes; on the service path the gap is one IPC round trip plus the `StopMove` call. On the fallback path, until the new `StopMove` arrives (process start + DDS init), the robot has received no stop command. Whether the Go2 sport service stops on its own when `Move` commands stop arriving is **unverified on the robot**. Until the checklist has measured it, assume the robot may keep moving at the last commanded velocity (≤ 0.3 m/s, or ≤ 1 rad/s when turning) for that time.

### Measured latency

Every `stop_move` record holds `stop_path` and, after a kill, the dispatcher-measured `kill_to_stop_ms` ([run-log.md](../dispatcher/docs/run-log.md#stop_move)). A service stop reports `stop_sent.stop_call_ms` (the `StopMove` call in the service); a fallback stop reports the `stop_move` utility's own `timing` ([skills.md](../skills/docs/skills.md#response-schema)). There is no required number; the warm service stop is assumed to take tens of ms, which the 500 ms `stop_sent` wait is set well above. The figures below are fresh-process stops, the fallback path. On the robot, successful stops measured `stop_call_ms` 891–900 ms from utility start, which includes DDS init and client creation ([reading records without `dds_init_ms`](../skills/docs/skills.md#response-schema)), and `total_ms` 1404–1428 ms (3 of 8 calls, [robot.md results](../skills/docs/robot.md#results), row 5); the other 5 failed from link loss ([known issues](../skills/docs/robot.md#known-issues)).

## Busy-reject

One task runs at a time. While it runs, any other message gets `Busy: a task is running. Send "stop" to stop it.` There is no queue and no interrupt by a new task; only the stop word gets through. The system assumes a single operator. The Telegram bot handles updates concurrently, because otherwise `stop` could not arrive during a task ([running.md](running.md)).

## The robot is stationary during LLM calls

Steps run only between LLM calls, never during one. When the dispatcher calls the model, the previous step has finished (its motion loop ended with `StopMove()`) or has been killed and stopped. A slow or failing LLM call never leaves the robot moving.

**No LLM call without a confirmed stop, within a task.** After a failed stop, the task ends with no further LLM call; a later task is not gated ([StopMove failure](#stopmove-failure)). This covers the executor's stops and the `StopMove`s the robot service sends on its own (ending a `move` primitive, at the lease deadline, on the lease connection's EOF, on `disarm`). The service reports the lease's latest one in the `disarmed` reply's `stop_failed` if it failed ([skills.md](../skills/docs/skills.md#robot-service)); the executor then runs the fallback `stop_move`, as after a failed `stop_sent`, and a failed fallback ends the task `STOP_FAILED` ([loop-and-context.md](../dispatcher/docs/loop-and-context.md#stop-failed)).

## Motion budget

Each task may command at most `motion_budget.max_distance_m` (10 m) of travel and `motion_budget.max_rotation_deg` (720°) of rotation (both *tunable*). Walk costs its distance, turn its angle, other skills nothing ([skills.md](../skills/docs/skills.md#policies)).

- The budget bounds **commanded** motion, not measured motion. Walk and turn are open-loop (velocity × time), so the real distance can differ.
- It is charged when a step is **dispatched**, even if the step then fails or is killed, because commanded motion may already have happened. Steps rejected before running are not charged.
- A plan whose steps up to its checkpoint would go over the limit is rejected **before any step runs**, as one failure. The mechanics are in [loop-and-context.md](../dispatcher/docs/loop-and-context.md#whole-plan-pre-check).
- Usage is shown to the model on every call (`Travel: 2 of 10 m used`).

## Bounds

Every step of a plan is checked against its `SKILL.md` declaration before anything runs: walk 0.1–3.0 m, turn 5–180°, enum values, required params, no unknown skills or params. A violation rejects the whole plan (one failure, then a replan). Details in [loop-and-context.md](../dispatcher/docs/loop-and-context.md#step-bounds). Skills do not check params at all, so a skill run by hand is not range-limited ([skills.md](../skills/docs/skills.md#invocation)).

## If the dispatcher dies

The robot service stops the robot: the dispatcher's death closes the control connection, and the service then ends any lease and sends `StopMove`. Its orphan watchdog (`GO2_PARENT_PID`, checked every 0.2 s) is the backup: it ends the lease, sends `StopMove`, and exits with code 137 about 2 s later. Skill processes run the same watchdog and exit with code 137; their motion runs in the service, so they do not stop the robot themselves. The watchdog does nothing when `GO2_PARENT_PID` is unset (manual runs). `stop_move` and `read_state` do not watch their parent, so a `StopMove` always finishes. The [vision service](../skills/docs/skills.md#vision-service) runs the same watchdog and never commands motion. Every normal exit of the transports calls `Dispatcher.shutdown()`, which closes the control connection and stops the service.

If the dispatcher host loses power or the network drops, none of this helps, and the operator can no longer send `stop`. Use the physical stop, which is why it must be within reach at all times ([above](#supervised-operation-rules)).

## Single-instance lock

`run`, `batch`, `--reset-stub` and `go2 bot` take an exclusive, non-blocking `flock` on `{log.dir}/.dispatcher.lock` and hold it until they exit. A second one prints `Another dispatcher is running (lock: <path>).` and exits 2. `catalog` and `state` do not take the lock; `state` only reads.

## What the LLM cannot do

- Run anything that is not a declared skill with declared params in range.
- See or invoke `stop_move` or `read_state`.
- Exceed the motion budget, the horizon, the call budget or the task time limit.
- Receive raw stderr, tracebacks or provider error text. Every problem reaches it as one short line.

## Design decisions

- **One stop path** (kill the process group and `stop` through the robot service, with `StopMove` from a fresh process as fallback) for operator stop, step timeouts, the task time limit, shutdown, internal errors and a lost service. One tested mechanism; a killed process cannot clean up after itself. The service's warm sport client stops in tens of ms instead of a process start plus DDS init.
- **The fresh-process `stop_move` stays as a permanent fallback.** The stop path must work when the service hangs or is dead, and a fresh process has its own DDS participant. Rejected: dropping it once the service stop exists (a hung service would leave no software stop).
- **The service stop and the motion share one sport client.** Replies are matched per request id and `Move` never waits for one, so a stop is never queued behind motion. Rejected: a separate stop client in the service (more DDS state, no gain).
- **A lost service is stopped first, then ends the task; the next task's state read restarts it.** The model cannot fix an infrastructure fault, and the robot must not keep moving on a service that may crash again. Rejected: replanning after a confirmed stop and a fresh read (a wasted call, hides crashes during development, and adds infrastructure noise to the replan metrics in the study data); restarting right after the loss (saves about 1 s on the next prompt, adds code and a failure mode between tasks).
- **The stop word bypasses busy-reject**, because a stop must reach a running task. Accepted variants: any case, surrounding spaces trimmed, and Telegram `/stop`.
- **Busy-reject: no queue, no interrupt by new tasks.** It keeps one task, one plan and one log at a time; the system assumes a single operator. Forwarding mid-run messages to the model is a future idea ([roadmap.md](roadmap.md#future-ideas)).
- **A per-task cumulative motion budget, separate for travel and rotation.** Per-step bounds alone would let ten 3 m walks through. It bounds commanded motion because v1 has no trusted position measurement; estimating position from commanded velocity (dead reckoning) was rejected, because it restates the walk skill's own open-loop assumption. A geofence waits for a real position source ([roadmap.md](roadmap.md#verifiability-per-skill)).
- **Charge the budget at dispatch, however the step ends**, because commanded motion may have happened even if the step errors or is killed.
- **The robot service stops the robot if the dispatcher dies**, on EOF of the control connection and through its orphan watchdog; skills exit through the same watchdog.
- **One dispatcher process per machine (file lock)**, so two processes can never drive one robot, overwrite the stub state, or interleave `index.jsonl`.
- **The robot is stationary during LLM calls**, so a slow or failing LLM call never leaves the robot moving.
- **A failed stop ends the task with no further LLM call in that task; with the service alive the outcome is `STOP_FAILED`.** The robot may still be moving, the model cannot fix that, and a person must act. Rejected: reusing `ROBOT_SERVICE_LOST` (the service is alive, so the study would miscount service losses); keeping only the warning on the final message (an LLM call with the robot possibly moving).
- **After a failed stop, the next task is not gated; the warning on the final message is the only safeguard across tasks** (user decision). The operator stays responsible for stopping the robot before the next task, and there is no gate code to maintain. Rejected: a task-start motion check (the fresh state read shows a velocity, the dispatcher stops the robot and ends the task); a persistent latch the operator must clear before the next task runs.
- **`go2 batch` stops after any task whose stop failed, whatever its outcome** (user decision). No operator reads the warning between batch lines, so the next line would call the model with the robot possibly moving. The check is `stop_move_failed` on the task outcome, which covers `STOP_FAILED`, `TIME_LIMIT_EXCEEDED`, `STATE_UNAVAILABLE`, `INTERNAL_ERROR` and the others alike ([running.md](running.md#output)). Rejected: stopping only at `STOP_FAILED` and `ROBOT_SERVICE_LOST`.
- **Agent limits are permission rules, not only CLAUDE.md prose**, because instructions are context, not enforcement. Bash argument patterns can be bypassed (`sh -c`, a changed config file); a PreToolUse guard hook is pending ([roadmap.md](roadmap.md#future-ideas)).
- **A physical stop within reach whenever the system runs on the onboard computer** (the robot's remote or a separate stop button). The operator sends `stop` over Wi-Fi, and if the Wi-Fi drops it no longer reaches the dispatcher, while a running skill may keep the robot moving until its motion loop ends.
- **The physical/remote e-stop remains the final safety measure.** The software stops are best effort, and every one depends on the robot link: with the link down no software stop reaches the robot and only the e-stop stops it ([known issues](../skills/docs/robot.md#known-issues)). Their latency is measured only for the first successful calls ([measured latency](#measured-latency)).
