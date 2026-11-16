# Execution

How the dispatcher runs one step: the executor and its parts in `dispatcher/execution/`. What the stop path does and why is in [safety.md](../../docs/safety.md#the-stop-path); the robot and vision service lifecycles are in [running.md](../../docs/running.md#robot-service); the step outcomes the loop sees are in [loop-and-context.md](loop-and-context.md#step-outcomes).

## Parts

Each part has one job and is tested without the others.

| Part | Module | Job |
|---|---|---|
| `ChildEnv` | `env.py` | Builds every child environment from `os.environ` at call time, minus the secrets (`SECRET_ENV`, [configuration.md](../../docs/configuration.md)), plus the contract variables: `for_skill(fault, lease_token)`, `for_service()`. |
| `SkillProcess` | `process.py` | One `python -m` skill process in its own process group, with two pipe-reader threads; `poll()`, `kill()` (SIGKILL to the group), `join()` (the readers), `finish(skill_name) -> ProcessRun` (parses the reply). The module also holds `parse_response`, `stderr_tail` and `run_utility(utility, env, cwd) -> UtilityRun` for `stop_move` and `read_state`. |
| `KillSwitch` | `kill.py` | The current process and its `Kill(cause, at_mono)` under one lock: `register`, `kill`, `kill_current`, `poll` (exit code and kill read together), `release`. |
| `ControlLink` | `control.py` | The dispatcher's one control connection to the robot service and its lock: `open()` (start-up, raises `RobotServiceError`), `arm`, `disarm`, `ping`, `stop` (each raises `ServiceLost(reason)` when the connection cannot be used), `close()`. |
| `StopPath` | `stop_path.py` | Every stop the executor sends, each returning one `StopMoveResult` and never raising: `stop(reason, kill)`, `after_kill(kill, lost)`, `disarm()`, `lost(service_error, t0, kill)`, `fallback(...)`. |
| `step_outcome` | `outcome.py` | A started step's `StepVerdict` (outcome, interrupt cause, error code and message) from its kill cause, the service loss and the skill's reply. |
| `Executor` | `executor.py` | Receives the parts and runs one step: arm, spawn, watch, stop or disarm, outcome. Also the `SkillExecutor` methods the loop calls (`kill_current`, `stop_move`, `fresh_state`, `state_stream`, `close`). |
| `build_executor(cfg, cwd)` | `dispatcher/app.py` | Wires the parts; `cwd` defaults to `cfg.base_dir`. The composition root ([running.md](../../docs/running.md#design-decisions)) also holds `read_robot_state(cfg)` (the `read_state` utility) for `go2 state`. |

`SkillExecutor` and `StreamHandle` (the protocols the loop and its fakes use) are in `dispatcher/execution/__init__.py`. The supervisors (`supervisor.py`, `robot_service.py`, `vision_service.py`) and the state stream (`state_stream.py`) are in the same package ([running.md](../../docs/running.md#design-decisions)).

Starting values: `POLL_INTERVAL_S` 0.05 s (watch-loop period, `executor.py`), `LIVENESS_PERIOD_S` 0.5 s and `STOP_WAIT_S` 0.5 s (`control.py`, both *tunable*), `READER_JOIN_TIMEOUT_S` 2 s and `STDERR_TAIL_CHARS` 2000 (`process.py`).

## A step

`Executor.run` does, in order:

1. For a skill that declares `uses_vision`, wait for the vision service (`VisionSupervisor.ensure_ready`); the wait comes off the remaining task time.
2. Write a robot fault (`ROBOT_FAULTS`) to the stub fault file, before the arm ([loop-and-context.md](loop-and-context.md#design-decisions)).
3. `ControlLink.arm(token, timeout)`. On `ServiceLost` the step never starts: `StopPath.lost` runs the fallback and the step is an `error` with `robot_service_lost` and no process.
4. Spawn the `SkillProcess` with the skill fault and the lease token, and register it with the `KillSwitch`.
5. Watch every `POLL_INTERVAL_S`: read the exit code and the kill together. After the first kill, `StopPath.after_kill` sends the stop at once, in parallel with the SIGKILL. Until a kill, each pass either pings the service (every `LIVENESS_PERIOD_S`; on `ServiceLost` it SIGKILLs the service, then kills the skill with `robot_service_lost`), or kills the skill for an operator stop, the task time limit or the step timeout, in that order. The loop ends when the process has exited, and the switch is released.
6. Without a stop from step 5: a loss seen after the skill exited runs `StopPath.lost`; otherwise `StopPath.disarm` ends the lease and returns a stop only if the service was lost or reports a failed `StopMove` of its own.
7. `SkillProcess.join` waits for the readers; `duration_ms` runs from before the arm to here. `SkillProcess.finish` then parses the reply.
8. `step_outcome` decides the outcome; the `ExecResult` holds it, the `ProcessRun` and the stop.

## Results

`ExecResult` (`models.py`) is `outcome`, `interrupt_cause`, `error_code`, `error_message`, `duration_ms`, `process: ProcessRun | None` (None when the step never started) and `stop_move`. `ProcessRun` is the skill process's `response`, `exit_code`, `pid` and `stderr_tail`. `ExecResult.service_lost` is read-only: true iff `stop_move` is set and its `service_lost` is true. The loop builds the `StepResult` from these fields and stores the same `ProcessRun`; the `step_result` record still holds `response`, `exit_code`, `pid` and `stderr_tail` at the top level ([run-log.md](run-log.md#design-decisions)).

The result types live in `models.py`, not in this package: `planning` and `llm` import `models`, and the import contracts forbid both from reaching `dispatcher.execution`.

## Design decisions

- **The executor is split into parts with one job each, and receives them.** The environment, the process, the kill switch, the control connection, the stop path and the outcome are each described in one sentence and tested alone: the stop path against a fake control link, the kill switch against a fake process, the fault routing against a fake `ControlLink`. The safety-critical code (the stop path) is one short module whose order of actions can be read top to bottom. `build_executor` is the one place that wires them. Rejected: one `Executor` class holding the environment, the pipes, the kill lock, the control connection, the stop path and the outcome mapping, whose tests had to monkeypatch private methods.
- **`service_lost` has one owner, `StopMoveResult.service_lost`.** A loss is always handled by a fallback stop that SIGKILLs the service, and that stop records it; `ExecResult.service_lost` is derived from it, so the two cannot disagree and no stop method returns a `(result, lost)` pair. Rejected: a separate flag on `ExecResult` set beside each stop, which every stop path had to keep in step with the record.
- **`ControlLink` raises one `ServiceLost(reason)` for every unusable connection** (service not running, unreachable, refused, no reply in time), so callers handle one exception and `reason` is the stop record's `service_error`. `open()` is the exception: it is a start-up step and raises `RobotServiceError`, a start-up error ([running.md](../../docs/running.md#robot-service)).
- **The stop path never raises and returns a plain `StopMoveResult`** from every method, so the executor and the loop never guard it; a fallback that cannot start is a failed result with the error in `stderr_tail` ([safety.md](../../docs/safety.md#the-stop-path)).
