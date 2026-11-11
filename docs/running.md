# Running

Operating the system. There are two entry points over the same dispatcher: `go2` (CLI) and `go2 bot` (Telegram). Run them with `uv run`, or activate `.venv` first. Installing is in [setup.md](setup.md); what the dispatcher does with a task is in [loop-and-context.md](../dispatcher/docs/loop-and-context.md); stopping and limits are in [safety.md](safety.md).

## CLI: `go2`

```
go2 [-h] [--config CONFIG] [--backend {stub,real}] [--horizon HORIZON]
    [--fault STEP:KIND] [--reset-stub] COMMAND ...

COMMAND: run TASK | batch TASKS_FILE | catalog | state | report [FOLDER] | bot
```

`go2 bot` starts the Telegram transport ([below](#telegram-bot)) and hands every argument after `bot` to it; it takes only its own `--config`, so a global option before `bot` is a usage error.

Global options go **before** the command (argparse), for example `go2 --backend real state`, not `go2 state --backend real`. Every command accepts every global option; only `report` has options of its own ([below](#latency-report)). Exactly one of a command or `--reset-stub` is required; `--reset-stub` with a command is a usage error. `-h` / `--help` works globally and after each command.

| Option | Meaning |
|---|---|
| `--config CONFIG` | Config file (default `./config.toml`; see [configuration.md](configuration.md)). |
| `--backend {stub,real}` | Overrides `robot.backend`. |
| `--horizon HORIZON` | Overrides `loop.planning_horizon` (an integer). |
| `--fault STEP:KIND` | Injects a stub fault at dispatched step `STEP`. Repeatable. Replaces `stub.faults` ([skills.md](../skills/docs/skills.md#fault-injection)). |
| `--reset-stub` | Resets the stub state file to `stub.initial_posture` and exits. |

| Command | Takes the lock | Resets the stub | Needs the API key | What it does |
|---|---|---|---|---|
| `run TASK` | yes | yes | yes | Runs one task, prints its live trace. |
| `batch TASKS_FILE` | yes | yes | yes | Runs one task per line, in order, in one process. |
| `--reset-stub` | yes | yes | no | Resets the stub and exits. |
| `catalog` | no | no | no | Prints the system text, `## Skills` + catalog, `## Tool schema` + the JSON schema, and `Registry hash: <16 hex>`. |
| `state` | no | no | no | Runs the `read_state` utility (its own DDS read, not through the robot service) and prints the robot state as JSON. |
| `report [FOLDER]` | no | no | no | Prints a latency report from existing run logs ([below](#latency-report)). |

"The API key" is the variable for `llm.provider`: `ANTHROPIC_API_KEY` or `OPENROUTER_API_KEY` ([configuration.md](configuration.md#env-file)). `run`, `batch` and the bot log each task under `{log.dir}/{llm.provider}/{robot.backend}/` ([run-log.md](../dispatcher/docs/run-log.md#files)).

Use `catalog` to see exactly what the model is sent before any task text. The hash identifies the prompt surface (system text + catalog + tool schema) and is logged with every task.

### Output

`run` and `batch` print a live trace of each task to stderr; it is their only task output, and stdout carries no outcome text. One line per event: `Thinking...` while the model is called, then a dim `Thought for 5.3s · {in} in / {out} out tokens`; the plan, one step per line (a `DONE` or `ABORT` plan shows its message); `▶ skill(params)` while a step runs, then `✓` or `✗` with the step rendered as in the LLM context ([loop-and-context.md](../dispatcher/docs/loop-and-context.md#user-message)) and its duration; infrastructure retries, rejected plans, stop requests, StopMove and internal errors; and a bold summary as the last line (outcome, LLM calls, steps, tokens, duration). When the task ends without a plan line that showed its message (`STOPPED`, a budget, `LLM_INVALID`, an error), the operator message is printed once just before the summary, in the same color:

```
Posture: standing
Thought for 3.0s · 1700 in / 122 out tokens
Plan: PLAN, 1 step
  1. turn(direction=left, angle_deg=90)
✓ turn(direction=left, angle_deg=90) -> ok (0.3s)
Thought for 3.7s · 1737 in / 154 out tokens
Plan: DONE: The robot has successfully turned left 90 degrees as requested.
DONE · 2 LLM calls · 1 steps run, 0 failed · 3437 in / 276 out tokens · 6.9s
```

On a terminal the trace is colored and the `Thinking...` and `▶` lines are replaced in place; when stderr is not a terminal, or `NO_COLOR` is set, it is plain text with one line per event. The trace is `dispatcher/transports/console_trace.py`, an observer of the run log records ([run-log.md](../dispatcher/docs/run-log.md#observers)); the Telegram bot shows a shorter version in one message per model call ([below](#progress-message)). The machine-readable record of a task is its run log ([run-log.md](../dispatcher/docs/run-log.md)).

`batch` prints `Task {i}: {task}` to stdout before each task, with a blank line between tasks. It prints no aggregate after the last task.

`batch` stops after the first task whose stop failed (`stop_move_failed`, whatever the outcome, `STOP_FAILED` among them) or that ends `ROBOT_SERVICE_LOST`. The remaining lines do not run. It prints `Batch stopped at task {i}: the stop command to the robot failed.` (a failed stop, also when the service was lost) or `Batch stopped at task {i}: the robot service was lost.` to stderr, then one `Not run: task {j}: {task}` for each remaining task, and exits 1. Any other task moves on to the next line.

### Exit codes

| Code | When |
|---|---|
| 0 | `run` ended `DONE`; `batch` finished all lines with no failed stop and no task ending `ROBOT_SERVICE_LOST`; `catalog`; `state` read a state; `report` printed a report; `--reset-stub` succeeded. |
| 1 | `run` ended with any outcome other than `DONE` (including a stop by Ctrl+C); a `batch` task had a failed stop or ended `ROBOT_SERVICE_LOST` ([output](#output)); `state` could not read a state (`Robot state unavailable.`); `report` found no logs in the folder or no finished task in the date window. |
| 2 | Usage error (a bad `report` date among them); `Config error: ...`; `Registry error: ...`; `Missing ANTHROPIC_API_KEY.` (or `OPENROUTER_API_KEY`); `Another dispatcher is running (lock: <path>).`; `--reset-stub` with the real backend; `run` with an empty task; a tasks file that cannot be read. |
| 130 | Ctrl+C: a second Ctrl+C, Ctrl+C while no task runs, or a `batch` stopped by the first Ctrl+C. |
| 143 | SIGTERM. |

### Ctrl+C

The task runs in a worker thread. The main thread waits in 0.2 s steps so signals are handled quickly.

- **First Ctrl+C while a task runs:** the same as the operator `stop` word. The running skill is killed, StopMove is sent, and the task ends `STOPPED`. `Stopping.` is printed. `run` then exits 1. `batch` skips the remaining lines and exits 130, also when that task had a failed stop or ended `ROBOT_SERVICE_LOST`; it prints no `Batch stopped` line.
- **Second Ctrl+C, or SIGTERM:** `dispatcher.shutdown(0)` kills the skill and sends StopMove, then the process exits with 130 (SIGINT) or 143 (SIGTERM).

### `batch` file format

Plain UTF-8 text, one task per line. Blank lines and lines that start with `#` (after leading spaces) are skipped. Each line is trimmed.

```
# warm-up
turn left 90 degrees
walk forward one metre, then tell me if you see a person
sit down
```

Tasks run one after another in one process. The previous task's summary **carries over** from line to line, as it does between Telegram messages; the robot state does not: each task reads a fresh one through the [robot service](#robot-service). One `sit` affects every later task, because no skill can stand the robot up ([robot.md](../skills/docs/robot.md#open-robot-side-questions)). A reset per task is a future idea ([roadmap.md](roadmap.md#future-ideas)).

### Latency report

```
go2 report [FOLDER] [--since WHEN] [--until WHEN] [--model NAME]
go2 report logs/openrouter/real --since 2026-10-29T14:57 --until 2026-10-29T17:00
go2 report --model claude-sonnet-5-5
```

Reads one log folder and prints a table to stdout, in seconds, one section per model, registry hash and condition: per call type, per task, and the time split. It reads logs only: no API key, lock, robot or network.

- `FOLDER`: default `{log.dir}/anthropic/real`, the study folder. An explicit folder does not read the config.
- `--since` / `--until`: `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM`, local time as recorded at task start, both ends included. A bare `--until` date covers that whole day.
- `--model`: only tasks whose `index.jsonl` `model` is exactly `NAME`. No matching task exits 1 with a message.

The metric definitions and what is skipped are in [run-log.md](../dispatcher/docs/run-log.md#latency-report-go2-report).

## Telegram bot

```
go2 bot [--config PATH]
```

### Setup

1. In Telegram, talk to **@BotFather**: send `/newbot` and follow the prompts. It gives you a token like `123456789:AA...`.
2. Put the token in `.env` in the base dir (or export it): `TELEGRAM_BOT_TOKEN=123456789:AA...`. Also set the API key for `llm.provider` (`ANTHROPIC_API_KEY` or `OPENROUTER_API_KEY`).
3. Find your numeric user id. One way: message **@userinfobot**, which replies with your id. Another: start the bot, send it any message, and read the stderr line `Warning: ignoring Telegram message from unauthorised user <id>.`
4. Add the id to the config:
   ```toml
   [telegram]
   allowed_user_ids = [123456789]
   ```
5. Run `uv run go2 bot --config config.toml`. The bot uses long polling, so it needs no public address.

At startup, `go2 bot`:
- loads the config and `.env`;
- exits 2 with `Missing TELEGRAM_BOT_TOKEN.` if there is no token;
- warns if `allowed_user_ids` is empty;
- takes the lock, resets the stub (stub backend only), and builds the LLM client (exits 2 with `Missing <API key variable>.` if the provider's key is missing).

Stop the bot with Ctrl+C (or SIGTERM). If a task is running, it is stopped at once, as if `stop` had been sent: the skill is killed, StopMove is sent, the task ends `STOPPED`, and its outcome is still replied. Then the bot shuts down. As a backstop, shutdown waits up to the `stop_move` `POLICY` timeout + 5 s (17 s) for the task to end, then kills it and sends StopMove itself. The bot is built on `python-telegram-bot` 22.x (pinned `>=22.8,<23`).

### Commands and replies

Messages from users who are not in `allowed_user_ids` get no reply (a warning line is printed to stderr). Edited messages are ignored. All replies and progress messages are sent with `parse_mode` HTML, and every dynamic part (model text, skill calls, plan messages, error codes) is escaped with `html.escape` and cut to 500 characters first (`PART_MAX_CHARS` in `dispatcher/transports/telegram_text.py`), so one line always fits in a message.

| You send | Reply |
|---|---|
| `/start` | `I control the Go2 robot. Send a task in plain words. Send "stop" to stop the current task. Backend: {backend}.` |
| `stop` (any case, spaces around it), or `/stop`, while a task runs | `🛑 Stopping.` The task's own reply follows with outcome `STOPPED`. |
| `stop` or `/stop` while idle | `ℹ️ Nothing is running.` |
| an empty message | `Send a task, for example: walk forward one metre.` |
| a task while another task runs | `⏳ Busy: a task is running. Send "stop" to stop it.` |
| a task | `🔵 Working on it.`, which then becomes the first progress message ([below](#progress-message)), then the outcome text ([below](#outcome-text)) as a new message |

If a handler raises an error, the bot replies `❌ Internal error in the bot. The details are in its console output.` and prints the traceback to stderr. The reply never names the exception.

Only `stop` is recognised during a task. Other messages sent during a task are answered `Busy` and are not forwarded to the model (forwarding them is a future idea; see [roadmap.md](roadmap.md#future-ideas)).

### Colour scheme

The Bot API cannot colour text, so a coloured emoji at the start of a line is the colour code:

| Emoji | Meaning | Used for |
|---|---|---|
| 🔵 | working | `Working on it.`, `Thinking...` |
| ℹ️ | information | plan title, `Nothing is running.` |
| ▶️ | running | the running step |
| ✅ | success | a step that ended `ok`, the `DONE` plan and outcome |
| ❌ | failure or error | a failed step, a rejected plan, a failed StopMove, every outcome that is not `DONE`, `ABORTED` or `STOPPED`, the internal error reply |
| ⚠️ | attention | the `ABORT` plan and `ABORTED` outcome, the StopMove failure warning |
| 🛑 | stop | stop requested, StopMove sent, `Stopping.`, the `STOPPED` outcome |
| ⏳ | wait | `Busy` |
| 🟢 / 🔴 | link up / link down | the [robot line](#robot-line) |

Titles are bold (`<b>Plan</b>`, `<b>Done</b>`, `<b>Stopped</b>`); `Thinking...` is italic. Plan steps are plain `{i}. {skill(params)}`.

### Progress message

Each model call has its own progress message, a round: `🔵 Thinking...` while the call runs, then that call's reply (its plan, a malformed-plan line, or `DONE` / `ABORT`) and the steps the plan ran. The first call uses the `🔵 Working on it.` reply; every later call in the task, schema retries included, sends a new message. The bot edits the newest message in place, and an older message keeps the final text of its round. Each edit replaces the whole text. A plan's title and its numbered steps are separated by an empty line, and so are the steps and the first line that follows them. A task whose first reply is malformed, then turns left and walks, then reports, shows three progress messages:

```
❌ The model produced a malformed plan; asking again
```
```
ℹ️ <b>Plan</b>: Turn first, then walk.

1. turn(direction=left, angle_deg=90)
2. walk(direction=forward, distance_m=1)

✅ turn(direction=left, angle_deg=90) -&gt; ok (0.3s)
✅ walk(direction=forward, distance_m=1) -&gt; ok (2.4s)
```
```
✅ <b>Done</b>: I turned left and walked one metre.
```

| Event | Line |
|---|---|
| model call running | `🔵 <i>Thinking...</i>` as the last line, removed when the reply arrives |
| plan `PLAN` | `ℹ️ <b>Plan</b>` (or `ℹ️ <b>Plan</b>: {message}` when the plan has a message), then `{i}. {skill(params)}` per step |
| plan `DONE` / `ABORT` | `✅ <b>Done</b>: {message}` / `⚠️ <b>Aborted</b>: {message}` |
| reply rejected by validation, or plan rejected by the bounds check | `❌ The model produced a malformed plan; asking again` |
| plan rejected by the motion budget | `❌ The plan would exceed the motion budget; asking again` |
| step running | `▶️ {skill(params)}` as the last line, replaced by the result |
| step result | `✅ {skill(params)} -> ok` with the observations the LLM context shows, or `❌ {skill(params)} {reason}` ([Outcome text](#outcome-text)), then ` ({seconds:.1f}s)` |
| stop request | `🛑 Stop requested ({source})` (`telegram` or `shutdown`) |
| StopMove | `🛑 StopMove sent ({seconds:.1f}s)` or `❌ <b>StopMove failed</b> ({seconds:.1f}s)` |
| state stream | one robot line, always last, in the newest message only ([below](#robot-line)) |

A rejected-plan line ends in `asking again` when the model is called again and in `giving up` when the rejection ends the task (`LLM_INVALID`, or the failure budget). The line waits for that next event, which follows the rejection at once. Left out: token counts, `Thought for …` timing, infrastructure retries, validation errors, skill error messages, internal errors and the posture line. They are in the run log and the CLI trace. The texts are fixed (`PROGRESS_*` in `dispatcher/prompts.py`) and identical across conditions. They are not part of the prompt surface, so they do not change the registry hash. If a message grows past 4000 characters, its oldest lines are replaced by `({n} earlier lines omitted)`. Every line is whole markup (each tag opens and closes in the same line), so dropping lines never cuts a tag.

The pump makes one Telegram call (an edit or a new message) at a time, at most one per second (`EDIT_INTERVAL_S` in `dispatcher/transports/telegram_progress.py`, tunable); events in between are merged into the next call. It edits a message only when its rendered text differs from the text of its last successful send or edit, and it sends the next round's message only after the older message shows its final text. When the task ends, the last state is sent, then the outcome text follows as a new message. A failed call prints `Warning: Telegram progress update failed: {ExceptionType}.` to stderr and is skipped; the next event tries again, and the task is not affected. The progress is `dispatcher/transports/telegram_progress.py`, the bot's run-log observer ([run-log.md](../dispatcher/docs/run-log.md#observers)). Its record methods only change the text and wake a sender on the bot's event loop. They never wait for Telegram, so sending does not slow the loop or the stop path and is not part of any measured LLM or step latency.

#### Robot line

While the task's [state stream](#robot-service) runs, the last line of the newest progress message shows the robot's latest state:

```
🟢 Robot: link up | standing | 0.42 m/s, turn 12 deg/s | obstacles m F 1.2 B - L 0.8 R 2.0
🔴 Robot: link down | standing | 0.42 m/s, turn 12 deg/s | obstacles m F 1.2 B - L 0.8 R 2.0
```

| Part | Content |
|---|---|
| link | `🟢 Robot: link up`, or `🔴 Robot: link down` when the service sees no state messages; the rest is then the last sample before the gap |
| posture | `RobotState.posture` |
| motion | ground speed `hypot(vx, vy)` in m/s, 2 decimals, and `yaw_speed` in deg/s, whole degrees; `speed n/a` without `velocity` |
| obstacles | nearest obstacle per direction from `obstacle_m` (front, back, left, right), metres to 0.1; `-` for a direction without a reading, `obstacles n/a` without `obstacle_m` ([robot.md](../skills/docs/robot.md)) |

Before the first sample it reads `🟢 Robot: link up | no state yet`. The line is rendered by `robot_line` in `dispatcher/transports/telegram_progress.py`. It is not a `PROGRESS_*` fixed text: it is never compared across runs and never reaches the model. The progress keeps only the latest sample and link, and wakes the sender only when the rendered line changed, so a robot at rest causes no edits. When a new round starts, the line moves to the new message and leaves the older one. At `task_end` the line is removed and later samples are dropped, so the final text holds only the task's lines, with no state frozen at an arbitrary moment. There is no line between tasks, and none when the task has no log file (no stream then).

### Outcome text

The reply that ends a task (`format_outcome` in `dispatcher/transports/telegram_text.py`):

```
✅ <b>Done</b>: I turned left 90 degrees and see a chair ahead, close by.

Steps: 2 run, 0 failed
1. ✅ turn(direction=left, angle_deg=90) -&gt; ok
2. ✅ detect_object(target=chair) -&gt; ok: object_found=true, position=center, closeness=near, confidence=0.9
```
```
❌ <b>Stopped</b>: the robot failed 3 times while trying this task.

Steps: 3 run, 3 failed
1. ❌ walk(direction=forward, distance_m=1) failed: the robot refused the move
2. ❌ walk(direction=left, distance_m=0.5) failed: the robot refused the move
3. ❌ walk(direction=right, distance_m=0.5) timed out

Last failure: walk timed out.
```

The first line is the outcome category (`TELEGRAM_OUTCOMES` in `dispatcher/prompts.py`): `DONE` and `ABORTED` carry the model's message; the others name what happened without the detail (`❌ <b>Stopped</b>: the model could not be reached.`, not the provider's error), and `INTERNAL_ERROR` names the run id so the log can be found. `Steps:` follows after an empty line and counts dispatched steps and failures (rejections count as failures). Then each recorded step is shown, numbered, or with `–` when it was rejected before running: an `ok` step as in the LLM context ([loop-and-context.md](../dispatcher/docs/loop-and-context.md#user-message)), a failed one as `❌ {skill(params)} {reason}`. After the list and an empty line, `FAILURE_BUDGET_EXHAUSTED` adds `Last failure: {skill} {reason}.`, and when a StopMove failed, `⚠️ <b>The stop command to the robot failed. Stop the robot manually.</b>` follows; without either the message ends with the list. If the whole text is longer than 4000 characters, the oldest step lines are replaced by `({n} earlier lines omitted)`; the title, the counts and the lines after the list are always kept.

A step's reason is a short category from `STEP_REASONS` in `dispatcher/prompts.py`, keyed by the step outcome, or by the skill's error code for outcome `error`:

| Key | Reason |
|---|---|
| `timeout` / `interrupted` / `malformed` | `timed out` / `was stopped` / `gave no valid answer` |
| `rejected` / `motion_budget_exceeded` | `was in a malformed plan` / `would exceed the motion budget` |
| `invalid_params` | `got invalid parameters` |
| `sdk_error` / `exception` / `backend_not_configured` | `failed: robot command error` / `failed: skill error` / `failed: backend not configured` |
| `motion_rejected` / `robot_unavailable` / `state_unavailable` / `robot_service_lost` | `failed: the robot refused the move` / `failed: robot unavailable` / `failed: robot state unavailable` / `failed: robot control service lost` |
| `camera_unavailable`, `bad_frame` / `vision_unavailable` / `detector_error`, `weights_missing` / `unsupported_object` | `failed: camera unavailable` / `failed: vision unavailable` / `failed: vision error` / `failed: object not supported` |
| any other error code | `failed: {code}` with underscores as spaces (`blocked` gives `failed: blocked`) |

The skill's error message, stderr and validation errors never reach Telegram; they are in the run log and the CLI trace.

## Stub vs real

| | Stub (`robot.backend = "stub"`, default) | Real (`robot.backend = "real"`) |
|---|---|---|
| Needs | core dependencies only | `uv sync --extra robot --extra vision`, `robot.network_interface`, YOLO weights ([setup.md](setup.md#lab-machine-real-robot)) |
| Motion | none; the stub keeps standing/sitting and an odometry pose integrated from `Move` in `stub.state_file` ([skills.md](../skills/docs/skills.md#stub-backend)) | real SDK calls ([robot.md](../skills/docs/robot.md)) |
| Durations | real durations × `stub.time_scale` | real time |
| `detect_object` | projects the objects in `stub.objects` from the current pose through a simulated camera ([skills.md](../skills/docs/skills.md#stub-backend)) | front camera + YOLO |
| Task-start state | read through the robot service (the stub is reset to `stub.initial_posture` at startup) | read through the robot service |
| Faults | `stub.faults` / `--fault` | not allowed (config error) |

The stub replaces only the SDK layer inside the skill process. Processes, timeouts, kills and StopMove run for real.

`go2 --reset-stub` puts the stub back in `stub.initial_posture`. Use it after a `sit`, because there is no `stand` skill. `run`, `batch` and `go2 bot` reset the stub once at startup (through `skills.stub.reset`), then start the [robot service](#robot-service), as on the real robot. Each task reads its state through it. The stub is not reset between tasks.

## Robot service

Skills read robot state through a long-running robot service, the only long-lived DDS subscriber of the robot's sport state ([skills.md](../skills/docs/skills.md#robot-service), which owns the protocol and the lifecycle contract). The dispatcher owns the service's lifecycle, on both backends (`dispatcher/robot.py`, held by the `Executor`); on the stub its sim source reads the stub state, so the process shape is the same.

- **Start.** `run`, `batch` and `go2 bot` start it at startup, after the stub reset, and wait for its ready line up to `STARTUP_TIMEOUT_S` (10 s, *tunable*). A failed start is a start-up error (exit 2) whose line names the reason and the service log: `Robot service not started: another robot service holds {log.dir}/robot.lock; stop it, or wait a few seconds (see ...)`, `Robot service not ready after 10s; stopped it (see ...)`, or `Robot service exited with code C before ready (see ...)` (code 2: a missing or unknown backend or interface). A service left by a killed dispatcher holds the lock until its orphan watchdog ends it, about 2 s later. `catalog`, `state` and `--reset-stub` never start it.
- **Socket and lock.** `{log.dir}/robot.sock`, guarded by `{log.dir}/robot.lock`: one service per `log.dir`. Every skill process gets the socket path in `GO2_ROBOT_SOCKET` (`child_env`).
- **Control connection.** Once the service is ready, the dispatcher opens its one control connection (`Executor.start_robot`; a failure is a start-up error) and keeps it open. Each step is armed on it with a lease token passed to the skill in `GO2_LEASE_TOKEN` and disarmed when the step ends; stops go through it ([safety.md](safety.md#the-stop-path)). Closing it makes the service send `StopMove`.
- **Lost mid-task.** A service that crashes or stops answering while a task runs is SIGKILLed, the robot is stopped by the fallback `stop_move`, and the task ends `ROBOT_SERVICE_LOST` ([safety.md](safety.md#robot-service-lost)). The service stays down until the next task's state read. The log line is `dispatcher: robot service lost; killed it`, then `... exited with code C; restarting` at that read.
- **State at task start.** Before every task's first planning call the dispatcher reads the latest state through the service (one socket round trip). A service that has exited since, or was lost in the previous task, is started once more first, and the control connection is opened again for the first step; that wait (up to 10 s) happens before the task's log opens. If the service cannot be started or reached, has had no state message, or reports the link down, the task ends `STATE_UNAVAILABLE` before any LLM call, with the operator message `Stopped: the robot state could not be read ({reason}).` and the reason in `task_start.state_error` ([run-log.md](../dispatcher/docs/run-log.md#task_start)). The next task tries again.
- **State stream.** While a task runs, the dispatcher subscribes to the service and writes the task's `state_stream.jsonl` ([run-log.md](../dispatcher/docs/run-log.md#state_streamjsonl)), and hands each written sample and link change to the task observer (the bot's [robot line](#robot-line)). One task uses one service process, so the stream covers the task, and a service loss ends both. A stream that fails alone, with the service fine, ends only the stream.
- **Stop.** `Dispatcher.shutdown()` closes the control connection and stops it on every exit, as for the vision service: `SIGTERM`, then `SIGKILL` after `STOP_GRACE_S` (2 s). If the dispatcher is killed, the closed control connection makes the service stop the robot, and its orphan watchdog ends it.
- **Log.** The service's stderr goes to `{log.dir}/robot_service.log`, with one line per dispatcher lifecycle event (`dispatcher: robot service started (pid N)`, `... exited with code C; restarting`, `... stopped (exit code C)`), emptied at the first start of each dispatcher process, as for the vision service.

`go2 state` does not use the service: it runs the `read_state` utility, which reads with its own DDS, so it works with or without a running dispatcher and diagnoses the robot link apart from the service.

## Vision service

`detect_object` asks a long-running vision service that holds the detector and the camera client ([skills.md](../skills/docs/skills.md#vision-service), which owns the protocol and the lifecycle contract). The dispatcher owns the service's lifecycle, on both backends (`dispatcher/vision.py`, held by the `Executor`):

- **Start.** `run`, `batch` and `go2 bot` start it at startup when the skill set contains a vision client (a skill whose entrypoint is `skills.detect_object`), after the robot service is ready. Startup does not wait for it; motion skills run while it warms up. `catalog`, `state` and `--reset-stub` never start it.
- **Ready wait.** Before each `detect_object` step the executor starts the service if it is not running (never started, or exited since), then waits for its ready line up to `STARTUP_TIMEOUT_S` (30 s, *tunable*) from that start. An operator stop ends the wait at once. The wait counts against the task time limit, not against the step timeout, which starts when the skill process starts.
- **Failed start.** A service that exits before its ready line, or is not ready after `STARTUP_TIMEOUT_S` (then it is stopped), does not end the task: the `detect_object` step runs and reports `vision_unavailable`, a step failure the model sees. The next `detect_object` step starts the service again. A failed detect is not retried.
- **Stop.** `Dispatcher.shutdown()` stops it on every exit: normal end, Ctrl+C, SIGTERM, Telegram shutdown, and an exception out of the transport (through `atexit`). `SIGTERM`, then `SIGKILL` after `STOP_GRACE_S` (2 s). Once stopped it is never started again in that process. If the dispatcher is killed, the service's orphan watchdog ends it ([safety.md](safety.md#if-the-dispatcher-dies)).
- **Log.** The service's stderr goes to `{log.dir}/vision_service.log`, together with one line per dispatcher lifecycle event (`dispatcher: vision service started (pid N)`, `... exited with code C; restarting`, `... not ready after 30s; stopping it`, `... stopped (exit code C)`). The file is emptied at the first start of each dispatcher process and appended to after that, so it holds the current or last session. These events are not in the run log ([run-log.md](../dispatcher/docs/run-log.md#files)).

## Fault injection

Stub faults (`stub.faults`, `--fault STEP:KIND`) are described in [skills.md](../skills/docs/skills.md#fault-injection).

## Switching skill sets

The registry loads every subfolder of `skills.dir` that contains a `SKILL.md`. To try a different skill set (for example another granularity tier), make a new folder with its own `SKILL.md` files and point the config at it:

```toml
[skills]
dir = "skill_sets/fine"
```

The registry hash changes with the catalog, so runs with different skill sets can be told apart in `index.jsonl`. Check a skill set with `go2 --config ... catalog`. A bad skill set exits 2 with `Registry error: <message naming the file>`. The `SKILL.md` format is in [skills.md](../skills/docs/skills.md).

## Single-instance lock

`run`, `batch`, `--reset-stub` and `go2 bot` take an exclusive `flock` on `{log.dir}/.dispatcher.lock` and hold it until they exit. A second one fails with:

```
Another dispatcher is running (lock: /path/to/logs/.dispatcher.lock).
```

and exit code 2. This stops two processes from driving one robot, overwriting the stub state file, or mixing lines in `index.jsonl`. `catalog` and `state` do not take the lock, so you can run them while a task is running. The lock is at the root of `log.dir`, so runs of every provider and backend under one root share it. It is tied to `log.dir`: two configs with different `log.dir` values do not block each other, so do not point two configs at the same robot.

## Design decisions

- **Two thin transports over one dispatcher.** The CLI and the bot only parse input, report the task and handle signals; everything else is shared, so CLI runs and Telegram runs produce the same logs. The Telegram integration replaces OpenClaw's channels ([architecture.md](architecture.md#why-a-purpose-built-dispatcher)).
- **The live trace is the CLI's only task output.** The trace already ends with the outcome, step counts, tokens and duration, and shows the operator message once (in the `DONE` or `ABORT` plan line, otherwise just before the summary), so printing the outcome text after it repeated the same task. Rejected: keeping the outcome text on stdout for scripting; the run log JSONL is the machine-readable record of every task ([run-log.md](../dispatcher/docs/run-log.md)). The bot keeps the outcome text, because its progress message leaves out the counts and the step list.
- **The bot shows progress in one message per model call, edited in place, and sends the outcome as a new message** (user decision). Without progress the operator saw nothing for the 15 to 60 s of a model call. A message per call makes each thinking round visible as its own message, with the plan next to the steps it ran, so a replan or a retry reads as a new attempt instead of a growing list; the old message keeps its final text as the record of that round. The last message holds the `DONE` or `ABORT` reply, and the outcome text that follows repeats its line with the step list; that repetition is accepted. Telegram limits a chat to about 1 message per second, so the pump makes one call at a time, at most one per `EDIT_INTERVAL_S` (tunable), and merges events in between. The record methods only update the text and the sending runs on the event loop, so Telegram never delays the loop, the stop path or the measured latencies. A failed call is printed and skipped, so Telegram trouble never fails a task. Rejected: one message per task, where a long task with replans scrolled its plans away; one message per `PLAN`, which hid the separate thinking rounds of a retry or the final reply; one message per step, which gives a notification per step and runs into the rate limit on short steps; a separate event bus, because the run-log observer already gives the typed records the CLI trace uses.
- **A message is edited only when its rendered text changed.** An identical edit costs a call against the rate limit and Telegram rejects it anyway. The robot line has no sample age for the same reason: an age changes every second and would force an edit every interval while nothing else moved. Rejected: a periodic refresh of the robot line, which spent the edit rate on a counter; showing the age at the edit, which made the line change without new data.
- **Emoji are the colour code, and titles use HTML bold.** The Bot API has no text colour; `parse_mode` HTML gives only bold, italic, code and quotes. One coloured emoji per line kind lets the operator scan a phone screen for red without reading. All dynamic text is escaped and every line is whole markup, so model text cannot break the message and trimming never cuts a tag. Rejected: MarkdownV2, whose escaping rules cover most punctuation in skill calls and model text; `<pre>` blocks, which lose the emoji's role and wrap badly on a phone.
- **Telegram shows categories, not technical detail.** The operator acts on what happened (the model's plan was malformed, the robot refused a move), not on a schema message or an SDK return code, and the detail is already in the run log and the CLI trace. A short reason per step outcome or error code keeps a failure readable on a phone; an unknown error code is still shown, with underscores as spaces, because a skill's code is a short word, not a message. Rejected: showing the step's `error_message`, which is written for the model and can be a validation or SDK text; hiding failures entirely, which leaves the operator unable to tell a refused move from a timeout.
- **`batch` stops at the first `ROBOT_SERVICE_LOST`.** A repeatedly crashing service would otherwise keep spending LLM calls and moving the robot, one line at a time. It exits 1, as `run` does for a task that does not end `DONE`. Rejected: running on, relying on the next task's restart.
- **`batch` stops after any task whose stop failed, whatever its outcome** (user decision). The next line would call the model with the robot possibly moving, and no operator reads the warning between lines ([safety.md](safety.md#design-decisions)). It exits 1 with the same stderr shape. Rejected: stopping only at `STOP_FAILED` and `ROBOT_SERVICE_LOST`; running on.
- **`batch` carries the previous task across lines**, because that is how the robot behaves between messages: it remembers. Experiments may need independent tasks later ([roadmap.md](roadmap.md#future-ideas)).
- **The bot stops a running task on SIGINT/SIGTERM before the Telegram library shuts down.** In python-telegram-bot 22.x, shutdown waits for every in-flight handler, so without this a running task would continue until it ended by itself. The bot replaces the library's signal handlers with one that calls the stop path, keeping `Dispatcher.shutdown()` as the backstop.
- **The dispatcher owns the vision service's lifecycle, through the `Executor`** (user decision), because the executor is what spawns `detect_object` and gives it the environment, so the service gets the same `child_env` and socket path by construction. `Dispatcher.shutdown()` closes the executor, so every exit path that already ends a task also stops the service. Rejected: a separate process supervisor or a transport-level hook beside `shutdown()`, which would be a second exit path to keep complete.
- **Startup does not wait for the vision service; the first `detect_object` step does.** Loading YOLO and the camera client takes about 5 s on the lab machine, and most tasks start with motion or with an LLM call, so the warm-up overlaps them. Rejected: blocking startup until ready (a slower start for every task, and a startup failure for a robot whose camera is down).
- **A vision service that fails to start does not end the task or the dispatcher.** The `detect_object` step reports `vision_unavailable` and the model replans, because a robot without vision can still do motion tasks; the next `detect_object` step tries a fresh start. Rejected: ending the task `INTERNAL_ERROR`, or refusing to start, both of which stop motion-only tasks for a camera fault.
- **The ready wait counts against the task time limit, not the step timeout.** The step timeout is the skill's own process budget ([skills.md](../skills/docs/skills.md#policies)); a cold start of up to 30 s would otherwise turn into a `timeout` kill instead of `vision_unavailable`.
- **The vision client is recognised by its entrypoint module (`skills.detect_object`), not by its catalog name**, so a skill set that renames the skill still gets the service. Rejected: a `SkillPolicy` flag, which would change the skills contract for one skill.
- **The service log is one file under `log.dir`, next to the run logs, emptied per dispatcher process.** It is for diagnosing a failed start or a crash, not study data, so it stays out of the provider/backend folders. The lifecycle events go into the same file rather than the run log, because the run log has no record type for them and its records are the study's contract ([run-log.md](../dispatcher/docs/run-log.md)). Rejected: an unread pipe (the service blocks once it fills); inheriting the dispatcher's stderr (YOLO and SDK output would interleave with the CLI trace).
- **Long polling**, so the bot needs no public address on the lab network.
- **Task-start state is read through the robot service on both backends**, and the dispatcher changes stub state only through `skills.stub.reset`. Reading the stub state file directly was rejected: it couples the dispatcher to stub internals (file path and format).
- **The dispatcher starts the robot service at startup and waits for it; a failed start stops the dispatcher.** Every skill's state samples and every task's first state come through it, so a dispatcher without it would run tasks blind; the lock error in particular means another process owns the robot. After startup, a service found dead at task start is started once more; if that fails, the task ends `STATE_UNAVAILABLE` ([loop-and-context.md](../dispatcher/docs/loop-and-context.md#design-decisions)) and the next task tries again. Rejected: starting it lazily like the vision service (the first task would pay the start and its state read would fail more often).
- **`go2 state` reads with the `read_state` utility, not through the service.** It needs no running dispatcher, and when the service misbehaves it shows whether the robot link itself works.
- **Start-up failures are typed errors; only the entry points print and exit.** `ConfigError` and `RegistryError` subclass `StartupError` (`dispatcher/models.py`); the lock, the planner choice and `build_dispatcher` raise them, and `cli.main` and the bot's `main` print the stderr line and exit 2. The entry points also own the process-lifetime hooks (`atexit` shutdown, signal handlers). Library code that printed and raised `SystemExit` was rejected: it could not be reused or tested without catching `SystemExit`, and `build_dispatcher` registered an `atexit` hook for a process it does not own. Separate subclasses for the lock and the planner were left out because no caller tells them apart.
