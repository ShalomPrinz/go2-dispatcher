# Configuration

Configuration is defined in `src/go2_dispatcher/config.py` (pydantic models); `config.example.toml` lists every key with its default. This page describes every key it accepts, where values come from, and how they are validated.

## Sources and precedence

1. Defaults in `config.py`.
2. A TOML file, `config.toml` by default or the path given with `--config PATH`.
   - If you do not pass `--config` and `./config.toml` is missing, the defaults are used and this warning is printed to stderr: `Warning: config.toml not found; using default configuration.`
   - If you pass `--config` and the file is missing, that is a config error.
3. CLI flags that override specific keys (see [CLI overrides](#cli-overrides)).

Start from the committed example: `cp config.example.toml config.toml`.

Secrets are never config keys. They come only from environment variables (see [`.env`](#env-file)).

## Base dir and paths

- **Base dir** is the folder that holds the loaded config file. If no config file was loaded, it is the current working directory.
- The four path keys (`skills.dir`, `log.dir`, `stub.state_file`, `robot.yolo_weights`) are resolved against the base dir when the config is loaded. A leading `~` is expanded. Absolute paths are kept as they are. The loaded `Config` holds absolute paths.
- `.env` is read from the base dir.
- Every skill and utility subprocess runs with the base dir as its working directory.
- `log.dir` is created if it is missing. If it cannot be created, that is a config error.

So `go2-dispatch --config /lab/exp1/config.toml run ...` writes logs to `/lab/exp1/runs/` and reads `/lab/exp1/.env`, whatever the current directory is.

## Keys

Every section and every model rejects unknown keys (`extra="forbid"`). *tunable* marks a starting value that is expected to be tuned from logs and robot runs (marked `(tunable)` in code comments and the example file).

Types:
- **int** keys reject floats, strings and booleans (`3`, not `3.0`).
- **float** keys also accept TOML integers (`300` is fine; stored as a float). They reject booleans, strings (also numeric ones such as `"1.5"`), `nan` and `inf`/`-inf`.
- **str** keys must be strings.

### `[run]`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `condition` | str | `""` | Free-text experiment label. Copied into `task_start` and `index.jsonl`. |

### `[llm]`

| Key | Type | Default | Rule | Meaning |
|---|---|---|---|---|
| `model` | str | `"claude-sonnet-5-5"` *tunable* | | Anthropic model id. Logged in `task_start.config` and `index.jsonl`. |
| `max_tokens` | int | `2048` | ≥ 1 | `max_tokens` for each request. |
| `thinking` | `"between_tools"` \| `"adaptive"` | `"between_tools"` | | Sent as `thinking={"type": ...}`. `between_tools` is the lowest setting (no extended thinking; [llm.md](llm.md#sonnet-55-parameter-constraints)); `adaptive` lets the model think (use it only as a deliberate experimental condition). Logged in `index.jsonl`. |
| `request_timeout_s` | float | `60.0` *tunable* | > 0 | Upper limit for one HTTP attempt. Each attempt uses `min(request_timeout_s, remaining task time)`. |
| `infra_max_retries` | int | `2` | ≥ 0 | Retries after transport or overload errors. Infra retries are not LLM calls. |
| `infra_backoff_s` | list of float | `[1.0, 4.0]` | each ≥ 0; length ≥ `infra_max_retries` | Seconds to sleep before retry 1, retry 2, and so on. A `retry-after` header can raise a sleep, capped at 30 s. |

There is no `temperature` key (adding one is an unknown-key error), and `tool_choice` is not configurable. The request these keys feed, and why it is shaped that way for Sonnet 5.5, is in [llm.md](llm.md#the-request). Read it before changing `llm.model`.

### `[loop]`

| Key | Type | Default | Rule | Meaning |
|---|---|---|---|---|
| `planning_horizon` | int | `5` *tunable* | ≥ 1 | Maximum steps in one plan. A plan with more steps is rejected, not truncated. Also the tool schema's `maxItems`. |
| `max_failures` | int | `3` *tunable* | ≥ 1 | The task ends `FAILURE_BUDGET_EXHAUSTED` when the failure count reaches this. |
| `max_llm_calls` | int | `20` *tunable* | ≥ 1 | LLM calls per task, schema retries included. When it is reached, the task ends `CALL_BUDGET_EXHAUSTED`. Its interaction with the horizon: [loop-and-context.md](loop-and-context.md#horizon-and-call-budget). |
| `task_time_limit_s` | float | `300.0` *tunable* | > 0 | Wall-clock limit per task. When it is reached, the task ends `TIME_LIMIT_EXCEEDED`. |
| `context_history_k` | int | `10` *tunable* | ≥ 1 | How many of the latest executed entries are shown in the context. Older entries are counted, not shown. |

### `[motion_budget]`

| Key | Type | Default | Rule | Meaning |
|---|---|---|---|---|
| `max_distance_m` | float | `10.0` *tunable* | ≥ 0 | Commanded travel allowed per task, in metres. |
| `max_rotation_deg` | float | `720.0` *tunable* | ≥ 0 | Commanded rotation allowed per task, in degrees. |

### `[skills]`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `dir` | path | `"skills"` | Skill set folder loaded by the registry. Each subfolder with a `SKILL.md` is one skill. |

### `[robot]`

| Key | Type | Default | Rule | Meaning |
|---|---|---|---|---|
| `backend` | `"stub"` \| `"real"` | `"stub"` | | Which backend the skill processes use. |
| `network_interface` | str | `""` | required (non-blank) when `backend = "real"` | NIC connected to the robot, for example `"enp0s31f6"`. Passed to skills as `GO2_IFACE`. |
| `yolo_weights` | path | `"models/yolov8n.pt"` | | YOLO weights for `detect_object` on the real backend. Never downloaded automatically. |
| `stop_move_timeout_s` | float | `10.0` | > 0 | Time limit for the `stop_move` utility process. |
| `read_state_timeout_s` | float | `10.0` | > 0 | Time limit for the `read_state` utility process. |

### `[stub]`

| Key | Type | Default | Rule | Meaning |
|---|---|---|---|---|
| `time_scale` | float | `0.1` | > 0 | Multiplies every simulated duration (motion loops, settle waits, detection delay). |
| `initial_posture` | `"standing"` \| `"sitting"` | `"standing"` | | Posture written to the state file at startup by `run`, `batch`, `go2-bot` and `--reset-stub`. |
| `state_file` | path | `"runs/.stub_state.json"` | | JSON file that holds the stub posture. Shared by all skill processes. |
| `detections` | table str → str | `{}` | keys are COCO class names; values match `^(left\|center\|right):(near\|medium\|far)$` | What the stub detector "sees". |
| `faults` | list of `{step, kind}` | `[]` | `step` int ≥ 1, unique; `kind` ∈ `error`, `hang`, `crash`, `garbage`; must be empty when `backend = "real"` | Faults injected by dispatched step number (counted across the task). See [skills.md](skills.md#fault-injection). |

COCO class names that contain a space must be quoted as TOML keys:

```toml
[stub]
detections = { chair = "center:near", "cell phone" = "left:far" }
faults = [ { step = 2, kind = "hang" } ]
```

The 80 class names are in `src/go2_skills/coco.py` (`COCO_CLASSES`).

### `[log]`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `dir` | path | `"runs"` | Run logs, `index.jsonl`, and the single-instance lock file `.dispatcher.lock`. |

### `[telegram]`

| Key | Type | Default | Meaning |
|---|---|---|---|
| `allowed_user_ids` | list of int | `[]` | Numeric Telegram user ids the bot answers. Empty means the bot answers nobody (it prints a warning at startup). |

## Validation rules

Each failure below is a config error:

- An unknown key in any section (reported as `<section>.<key>: unknown key`).
- A value of the wrong type (see the type notes above).
- `robot.backend = "real"` with a blank `robot.network_interface`.
- `robot.backend = "real"` with a non-empty `stub.faults`.
- `stub.faults[].kind` not one of `error | hang | crash | garbage`; `stub.faults[].step` not an integer ≥ 1; the same step listed twice.
- `stub.detections` key not in `COCO_CLASSES`, or a value that does not match `position:closeness`.
- Integers ≥ 1: `planning_horizon`, `max_failures`, `max_llm_calls`, `context_history_k`, `max_tokens`.
- Floats > 0: `task_time_limit_s`, `request_timeout_s`, `stop_move_timeout_s`, `read_state_timeout_s`, `stub.time_scale`.
- Floats ≥ 0: `max_distance_m`, `max_rotation_deg`, every value in `infra_backoff_s`.
- `llm.thinking` not one of `between_tools`, `adaptive`.
- Any float key given a boolean, a string, `nan` or `±inf`.
- `infra_max_retries` ≥ 0 and `len(infra_backoff_s) >= infra_max_retries`.
- Invalid TOML, an unreadable file, an explicit `--config` path that does not exist, or a `log.dir` that cannot be created.

## Exit codes

Any config error prints `Config error: <message>` to stderr and exits with code **2**. Several errors in one file are joined with `; `. Example:

```
$ go2-dispatch --config bad.toml catalog
Config error: loop.planning_horizon: Input should be greater than or equal to 1; loop.colour: unknown key
```

Other startup errors also exit with code 2 (registry error, missing API key or bot token, lock held). See [running.md](running.md#exit-codes).

## CLI overrides

`go2-dispatch` overrides these keys. They are applied before validation, so the same rules apply:

| Flag | Key |
|---|---|
| `--backend stub\|real` | `robot.backend` |
| `--horizon N` | `loop.planning_horizon` |
| `--fault STEP:KIND` (repeatable) | replaces `stub.faults` |

A malformed `--fault` value is a config error, and so is `--fault` together with the real backend. `go2-bot` takes only `--config`.

## `.env` file

Secrets come only from environment variables:

| Variable | Needed by |
|---|---|
| `ANTHROPIC_API_KEY` | `run`, `batch`, `go2-bot` (only when a real LLM client is built) |
| `TELEGRAM_BOT_TOKEN` | `go2-bot` |

A `.env` file in the base dir is read by a small built-in parser (no `python-dotenv`):

```
# comment
ANTHROPIC_API_KEY=sk-ant-...
export TELEGRAM_BOT_TOKEN="123456:ABC..."
```

- One `KEY=VALUE` per line. A leading `export ` is allowed.
- Blank lines and lines that start with `#` are ignored. Lines without `=` are also ignored.
- Keys and values are trimmed. One pair of matching single or double quotes around the value is removed. There is no interpolation.
- **Real environment variables win** over `.env` values.
- A missing `.env` is not an error.

Secrets are never written to the run log, never printed, and never passed to skill subprocesses: the executor removes `ANTHROPIC_API_KEY` and `TELEGRAM_BOT_TOKEN` from the child environment.

## Where the other tunables live

Some *tunable* values are named constants in code, not config keys:

| Value | Where |
|---|---|
| Per-skill timeouts and settle waits (`BASE_S`, `FACTOR`, `TIMEOUT_S`, `SETTLE_S`) | Policy class attributes in `src/go2_skills/<skill>.py` ([skills.md](skills.md#policies)) |
| Walking speed, yaw rate, command period | `VELOCITY_MPS`, `YAW_RATE_RPS`, `CMD_PERIOD_S` in `walk.py` / `turn.py` |
| Posture thresholds (0.15 / 0.22 m) | `POSTURE_SITTING_MAX_M`, `POSTURE_STANDING_MIN_M` in `src/go2_skills/posture.py` ([robot.md](robot.md#posture-rule)) |
| Detector thresholds | `RealDetector` constants in `src/go2_skills/real.py` ([robot.md](robot.md#object-detection)) |
| `StopMove` settle wait (0.5 s) | `SETTLE_S` in `src/go2_skills/stop_move.py` |
| Stub durations and body heights | Constants at the top of `src/go2_skills/stub.py` |

## Design decisions

- **Secrets only from environment variables or `.env`**, never config keys; real environment variables win; they are removed from skill processes and never logged. The full config can then be logged with every task (`task_start.config`) without leaking anything, and skills never see credentials.
- **Relative paths resolve against the config file's folder (the base dir), which is also every subprocess's working directory.** A config folder is self-contained, so a run behaves the same whatever the current directory is.
- **Unknown keys and loosely typed values are errors** (`extra="forbid"`, strict ints and finite floats), not warnings: a misspelt or mistyped key stops startup with exit code 2 instead of being ignored.
- **CLI overrides go through the same validation** as the file: they are applied to the raw data before the models are built.
- **Every starting value is a config key or a named constant**, never inline ([architecture.md](architecture.md#design-decisions)).
