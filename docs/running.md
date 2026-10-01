# Running

There are two entry points over the same dispatcher: `go2-dispatch` (CLI) and `go2-bot` (Telegram). Run them with `uv run`, or activate `.venv` first. Spec: §16.

## CLI: `go2-dispatch`

```
go2-dispatch [--config PATH] [--backend {stub,real}] [--horizon N] [--fault STEP:KIND ...] run TASK
go2-dispatch [same options] batch TASKS_FILE
go2-dispatch [--config PATH] --reset-stub
go2-dispatch [--config PATH] catalog
go2-dispatch [--config PATH] [--backend {stub,real}] state
```

Global options go **before** the command (argparse), for example `go2-dispatch --backend real state`, not `go2-dispatch state --backend real`. Every command accepts every global option. Exactly one of a command or `--reset-stub` is required.

| Option | Meaning |
|---|---|
| `--config PATH` | Config file (default `./config.toml`; see `docs/configuration.md`). |
| `--backend {stub,real}` | Overrides `robot.backend`. |
| `--horizon N` | Overrides `loop.planning_horizon`. |
| `--fault STEP:KIND` | Injects a stub fault at dispatched step `STEP`. Repeatable. Replaces `stub.faults`. |
| `--reset-stub` | Resets the stub state file to `stub.initial_posture` and exits. |

| Command | Takes the lock | Resets the stub | Needs `ANTHROPIC_API_KEY` | What it does |
|---|---|---|---|---|
| `run TASK` | yes | yes | yes | Runs one task, prints the outcome. |
| `batch TASKS_FILE` | yes | yes | yes | Runs one task per line, in order, in one process. |
| `--reset-stub` | yes | yes | no | Resets the stub and exits. |
| `catalog` | no | no | no | Prints the system text, `## Skills` + catalog, `## Tool schema` + the JSON schema, and `Registry hash: <16 hex>`. |
| `state` | no | no | no | Runs the `read_state` utility and prints the robot state as JSON. |

Use `catalog` to see exactly what the model is sent before any task text. The hash identifies the prompt surface (system text + catalog + tool schema) and is logged with every task.

### Output

`run` prints the outcome as plain text:

```
DONE: I turned left 90 degrees and see a chair ahead, close by.
Steps: 2 run, 0 failed
1. turn(direction=left, angle_deg=90) -> ok
2. detect_object(target=chair) -> ok: object_found=true, position=center, closeness=near, confidence=0.9
```

The first line is `{OUTCOME}: {message}`. `Steps:` counts dispatched steps and failures (rejections count as failures). Then each recorded step is shown, rendered as in the LLM context (`docs/loop-and-context.md`). If the whole text is longer than 4000 characters, the oldest step lines are replaced by `({n} earlier lines omitted)`.

`batch` prints `Task {i}: {task}` before each outcome, with a blank line between tasks.

### Exit codes

| Code | When |
|---|---|
| 0 | `run` ended `DONE`; `batch` finished all lines; `catalog`; `state` read a state; `--reset-stub` succeeded. |
| 1 | `run` ended with any outcome other than `DONE` (including a stop by Ctrl+C); `state` could not read a state (`Robot state unavailable.`). |
| 2 | Usage error; `Config error: ...`; `Registry error: ...`; `Missing ANTHROPIC_API_KEY.`; `Another dispatcher is running (lock: <path>).`; `--reset-stub` with the real backend; `run` with an empty task; a tasks file that cannot be read. |
| 130 | Ctrl+C: a second Ctrl+C, Ctrl+C while no task runs, or a `batch` stopped by the first Ctrl+C. |
| 143 | SIGTERM. |

### Ctrl+C

The task runs in a worker thread. The main thread waits in 0.2 s steps so signals are handled quickly.

- **First Ctrl+C while a task runs:** the same as the operator `stop` word. The running skill is killed, StopMove is sent, and the task ends `STOPPED`. `Stopping...` is printed. `run` then prints the outcome and exits 1. `batch` prints the outcome, skips the remaining lines and exits 130.
- **Second Ctrl+C, or SIGTERM:** `dispatcher.shutdown(0)` kills the skill and sends StopMove, then the process exits with 130 (SIGINT) or 143 (SIGTERM).

### `batch` file format

Plain UTF-8 text, one task per line. Blank lines and lines that start with `#` (after leading spaces) are skipped. Each line is trimmed.

```
# warm-up
turn left 90 degrees
walk forward one metre, then tell me if you see a person
sit down
```

Tasks run one after another in one process. The previous task's summary and the robot posture **carry over** from line to line (OD-6). One `sit` affects every later task, because no skill can stand the robot up (OD-1).

## Telegram: `go2-bot`

```
go2-bot [--config PATH]
```

### Setup

1. In Telegram, talk to **@BotFather**: send `/newbot` and follow the prompts. It gives you a token like `123456789:AA...`.
2. Put the token in `.env` in the base dir (or export it): `TELEGRAM_BOT_TOKEN=123456789:AA...`. Also set `ANTHROPIC_API_KEY`.
3. Find your numeric user id. One way: message **@userinfobot**, which replies with your id. Another: start the bot, send it any message, and read the stderr line `Warning: ignoring Telegram message from unauthorised user <id>.`
4. Add the id to the config:
   ```toml
   [telegram]
   allowed_user_ids = [123456789]
   ```
5. Run `uv run go2-bot --config config.toml`. The bot uses long polling, so it needs no public address.

At startup, `go2-bot`:
- loads the config and `.env`;
- exits 2 with `Missing TELEGRAM_BOT_TOKEN.` if there is no token;
- warns if `allowed_user_ids` is empty;
- takes the lock, resets the stub (stub backend only), and builds the LLM client (exits 2 with `Missing ANTHROPIC_API_KEY.` if the key is missing).

Stop the bot with Ctrl+C. On shutdown it waits up to `robot.stop_move_timeout_s + 5` s for a running task to stop (kill + StopMove).

### Commands and replies

Messages from users who are not in `allowed_user_ids` get no reply (a warning line is printed to stderr). Edited messages are ignored. All replies are plain text.

| You send | Reply |
|---|---|
| `/start` | `I control the Go2 robot. Send a task in plain words. Send "stop" to stop the current task. Backend: {backend}.` |
| `stop` (any case, spaces around it), or `/stop`, while a task runs | `Stopping.` The task's own reply follows with outcome `STOPPED`. |
| `stop` or `/stop` while idle | `Nothing is running.` |
| an empty message | `Send a task, for example: walk forward one metre.` |
| a task while another task runs | `Busy: a task is running. Send "stop" to stop it.` |
| a task | `Working on it.`, then the outcome text (same format as the CLI) |

If a handler raises an error, the bot replies `Error: {ExceptionType}` and prints the traceback to stderr.

Only `stop` is recognised during a task. Other messages sent during a task are answered `Busy` and are not forwarded to the model (a future idea; see `docs/future-ideas.md`).

## Stub vs real

| | Stub (`robot.backend = "stub"`, default) | Real (`robot.backend = "real"`) |
|---|---|---|
| Needs | core dependencies only | `uv sync --extra robot --extra vision`, CycloneDDS, `robot.network_interface`, YOLO weights (see `docs/setup.md`) |
| Motion | none; the stub remembers only standing/sitting in `stub.state_file` | real SDK calls |
| Durations | real durations × `stub.time_scale` | real time |
| `detect_object` | reports what `stub.detections` lists (confidence 0.9) | front camera + YOLO |
| Startup posture | reset to `stub.initial_posture` | read with `read_state` (`unknown` if that fails) |
| Faults | `stub.faults` / `--fault` | not allowed (config error) |

The stub replaces only the SDK layer inside the skill process. Processes, timeouts, kills and StopMove run for real.

`go2-dispatch --reset-stub` puts the stub back in `stub.initial_posture`. Use it after a `sit`, because there is no `stand` skill. `run`, `batch` and `go2-bot` reset the stub once at startup. The stub is not reset between tasks.

## Fault injection

Faults apply to the stub only. The step number counts **dispatched** steps across the whole task (1-based, across plans). Rejected steps are not counted. The fault hits the first action call the skill makes (`Move`, `StopMove`, `StandDown`, `Stretch` or `detect`).

| Kind | What the skill process does | Step outcome |
|---|---|---|
| `error` | the call returns code 99 (detector: camera error → `camera_unavailable`) | `error` |
| `hang` | sleeps forever | `timeout` (killed, StopMove sent) |
| `crash` | exits with code 139 without output | `malformed` |
| `garbage` | prints `not json` and exits 0 | `malformed` |

```bash
uv run go2-dispatch --fault 2:hang run "turn left, then walk forward one metre"
uv run go2-dispatch --fault 1:error --fault 3:crash batch tasks.txt
```

Or in config: `[stub] faults = [ { step = 2, kind = "hang" } ]`. `--fault` replaces the whole list.

## Switching skill sets

The registry loads every subfolder of `skills.dir` that contains a `SKILL.md`. To try a different skill set (for example another granularity tier), make a new folder with its own `SKILL.md` files and point the config at it:

```toml
[skills]
dir = "skill_sets/fine"
```

The registry hash changes with the catalog, so runs with different skill sets can be told apart in `index.jsonl`. Check a skill set with `go2-dispatch --config ... catalog`. A bad skill set exits 2 with `Registry error: <message naming the file>`. See `docs/skills.md` for the SKILL.md format.

## Single-instance lock

`run`, `batch`, `--reset-stub` and `go2-bot` take an exclusive `flock` on `{log.dir}/.dispatcher.lock` and hold it until they exit. A second one fails with:

```
Another dispatcher is running (lock: /path/to/runs/.dispatcher.lock).
```

and exit code 2. This stops two processes from driving one robot, overwriting the stub state file, or mixing lines in `index.jsonl`. `catalog` and `state` do not take the lock, so you can run them while a task is running. The lock is tied to `log.dir`: two configs with different `log.dir` values do not block each other, so do not point two configs at the same robot.
