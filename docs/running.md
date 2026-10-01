# Running

Operating the system. There are two entry points over the same dispatcher: `go2` (CLI) and `go2 bot` (Telegram). Run them with `uv run`, or activate `.venv` first. Installing is in [setup.md](setup.md); what the dispatcher does with a task is in [loop-and-context.md](../dispatcher/docs/loop-and-context.md); stopping and limits are in [safety.md](safety.md).

## CLI: `go2`

```
go2 [-h] [--config CONFIG] [--backend {stub,real}] [--horizon HORIZON]
    [--fault STEP:KIND] [--reset-stub] COMMAND ...

COMMAND: run TASK | batch TASKS_FILE | catalog | state | bot
```

`go2 bot` starts the Telegram transport ([below](#telegram-bot)) and hands every argument after `bot` to it; it takes only its own `--config`, so a global option before `bot` is a usage error.

Global options go **before** the command (argparse), for example `go2 --backend real state`, not `go2 state --backend real`. Every command accepts every global option; the commands take no options of their own. Exactly one of a command or `--reset-stub` is required; `--reset-stub` with a command is a usage error. `-h` / `--help` works globally and after each command.

| Option | Meaning |
|---|---|
| `--config CONFIG` | Config file (default `./config.toml`; see [configuration.md](configuration.md)). |
| `--backend {stub,real}` | Overrides `robot.backend`. |
| `--horizon HORIZON` | Overrides `loop.planning_horizon` (an integer). |
| `--fault STEP:KIND` | Injects a stub fault at dispatched step `STEP`. Repeatable. Replaces `stub.faults` ([skills.md](../skills/docs/skills.md#fault-injection)). |
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

The first line is `{OUTCOME}: {message}`. `Steps:` counts dispatched steps and failures (rejections count as failures). Then each recorded step is shown, rendered as in the LLM context ([loop-and-context.md](../dispatcher/docs/loop-and-context.md#user-message)). If the whole text is longer than 4000 characters, the oldest step lines are replaced by `({n} earlier lines omitted)`.

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

Tasks run one after another in one process. The previous task's summary and the robot posture **carry over** from line to line, as they do between Telegram messages. One `sit` affects every later task, because no skill can stand the robot up ([robot.md](../skills/docs/robot.md#open-robot-side-questions)). A reset per task is a future idea ([roadmap.md](roadmap.md#future-ideas)).

## Telegram bot

```
go2 bot [--config PATH]
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
5. Run `uv run go2 bot --config config.toml`. The bot uses long polling, so it needs no public address.

At startup, `go2 bot`:
- loads the config and `.env`;
- exits 2 with `Missing TELEGRAM_BOT_TOKEN.` if there is no token;
- warns if `allowed_user_ids` is empty;
- takes the lock, resets the stub (stub backend only), and builds the LLM client (exits 2 with `Missing ANTHROPIC_API_KEY.` if the key is missing).

Stop the bot with Ctrl+C (or SIGTERM). If a task is running, it is stopped at once, as if `stop` had been sent: the skill is killed, StopMove is sent, the task ends `STOPPED`, and its outcome is still replied. Then the bot shuts down. As a backstop, shutdown waits up to `robot.stop_move_timeout_s + 5` s for the task to end, then kills it and sends StopMove itself. The bot is built on `python-telegram-bot` 22.x (pinned `>=22.8,<23`).

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

Only `stop` is recognised during a task. Other messages sent during a task are answered `Busy` and are not forwarded to the model (forwarding them is a future idea; see [roadmap.md](roadmap.md#future-ideas)).

## Stub vs real

| | Stub (`robot.backend = "stub"`, default) | Real (`robot.backend = "real"`) |
|---|---|---|
| Needs | core dependencies only | `uv sync --extra robot --extra vision`, CycloneDDS, `robot.network_interface`, YOLO weights ([setup.md](setup.md#lab-machine-real-robot)) |
| Motion | none; the stub remembers only standing/sitting in `stub.state_file` | real SDK calls ([robot.md](../skills/docs/robot.md)) |
| Durations | real durations × `stub.time_scale` | real time |
| `detect_object` | reports what `stub.detections` lists (confidence 0.9) | front camera + YOLO |
| Startup posture | reset to `stub.initial_posture` | read with `read_state` (`unknown` if that fails) |
| Faults | `stub.faults` / `--fault` | not allowed (config error) |

The stub replaces only the SDK layer inside the skill process. Processes, timeouts, kills and StopMove run for real.

`go2 --reset-stub` puts the stub back in `stub.initial_posture`. Use it after a `sit`, because there is no `stand` skill. `run`, `batch` and `go2 bot` reset the stub once at startup. The stub is not reset between tasks.

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
Another dispatcher is running (lock: /path/to/runs/.dispatcher.lock).
```

and exit code 2. This stops two processes from driving one robot, overwriting the stub state file, or mixing lines in `index.jsonl`. `catalog` and `state` do not take the lock, so you can run them while a task is running. The lock is tied to `log.dir`: two configs with different `log.dir` values do not block each other, so do not point two configs at the same robot.

## Design decisions

- **Two thin transports over one dispatcher.** The CLI and the bot only parse input, format the outcome and handle signals; everything else is shared, so CLI runs and Telegram runs produce the same logs. The Telegram integration replaces OpenClaw's channels ([architecture.md](architecture.md#why-a-purpose-built-dispatcher)).
- **`batch` carries the previous task and posture across lines**, because that is how the robot behaves between messages: it remembers. Experiments may need independent tasks later ([roadmap.md](roadmap.md#future-ideas)).
- **The bot stops a running task on SIGINT/SIGTERM before the Telegram library shuts down.** In python-telegram-bot 22.x, shutdown waits for every in-flight handler, so without this a running task would continue until it ended by itself. The bot replaces the library's signal handlers with one that calls the stop path, keeping `Dispatcher.shutdown()` as the backstop.
- **Long polling**, so the bot needs no public address on the lab network.
