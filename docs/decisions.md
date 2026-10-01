# Decisions

Implementation choices where the spec was silent, and outcomes of checks the spec asks to record.

## T1 — Scaffold and configuration

- **§4.1 / OD-14 `robot` extra:** `uv lock` succeeded on the dev machine (WSL2, Python 3.10, no CycloneDDS installed) with the `robot` extra kept (`cyclonedds==0.10.2` resolved from PyPI, `unitree_sdk2py` from git). The manual-install fallback was **not** needed. `uv sync` (core + dev) also succeeds. `uv sync --extra robot` was not attempted here (it builds CycloneDDS).
- **Config overrides format:** `load_config(path, overrides)` / `build_config(data, base_dir, overrides)` take a dict of dotted keys, e.g. `{"robot.backend": "real", "loop.planning_horizon": 3, "stub.faults": [...]}`, applied to the raw TOML data before validation, so overrides go through the same validation.
- **`load_config_and_env` lives in `config.py`** (prints `Config error: ...`, exits 2, then loads `.env`). `transports/__init__.py` (T10) should re-export it rather than reimplement.
- **Base dir on `Config`:** stored as a private attribute, exposed as the read-only property `cfg.base_dir`, so it is not a config key.
- **Strict integers:** integer keys (`planning_horizon`, `max_failures`, `max_llm_calls`, `context_history_k`, `max_tokens`, `infra_max_retries`, `stub.faults[].step`, `telegram.allowed_user_ids[]`) reject floats, strings and booleans. Float keys accept TOML integers.
- **`.env` parser:** keys and values are whitespace-stripped; lines without `=` or with an empty key are ignored silently; a missing `.env` is not an error.
- **Missing default config warning:** `Warning: config.toml not found; using default configuration.` on stderr.
- **`log.dir` creation** happens in `build_config` (at load time, §5.3); failure to create it is a config error.
- **`go2_skills/coco.py` created in T1** (list from the build sequence) because `stub.detections` validation needs `COCO_CLASSES`.
- **Process lock:** prints its message to stderr; re-acquiring in the same process is a no-op; the file handle is kept in a module dict for the life of the process.
- **Test layout:** `tests/unit`, `tests/integration`, `tests/robot` have `__init__.py` (avoids basename clashes); `tests/` has none, so pytest's default rootdir insertion makes `helpers` importable (`from helpers import make_config`). No `sys.path.insert`.
- **`make_config(tmp_path, **overrides)`:** overrides are section dicts merged into the base data, e.g. `make_config(tmp_path, loop={"max_failures": 1})`.

## T2 — Skill runtime: helpers and backends

- **`backend_not_configured` everywhere:** `BackendNotConfigured` (raised by `backend.*` when `GO2_BACKEND` is missing/unknown, and by `real` when `GO2_IFACE` is empty) is never turned into a `state_error`; `run_skill` and both utilities map it to `code=backend_not_configured`, without a traceback on stderr.
- **Skill-side exceptions** live in `go2_skills/backend.py`: `BackendNotConfigured`, `StateUnavailable`, and `DetectorError` with subclasses `CameraUnavailable` / `BadFrame` / `WeightsMissing`, each carrying its error `code`. The stub's `StubCameraError` subclasses `CameraUnavailable`, so `detect_object` can map any `DetectorError` via `e.code`.
- **`result.InvalidParams`:** skills raise it from `body` (or `parse_params` raises it) and `run_skill` emits `invalid_params`. Missing `argv[1]` is also `invalid_params`.
- **`state_error` entries** are `"before: <Type>: <first line>"` / `"after: ..."`, joined with `"; "`.
- **On an exception in `body`**, `run_skill` emits without sampling `state_after`.
- **`build_response`** also raises `ValueError` for a status other than `ok`/`error` and for `ok` with an error code/message (mirrors the model validator). It always includes every key (unused ones are `null`/empty). `emit()` falls back to an `exception` error line if the response cannot be built or serialised, so a process still prints exactly one valid line.
- **`stop_move` sends `StopMove()` even when its params argument is invalid** (safety first), then reports `invalid_params`. `read_state` validates params before sampling. Both ignore faults by removing `GO2_STUB_FAULT` from their own environment at start.
- **`read_state` timing:** `state_ms`, `total_ms`. **`stop_move` timing:** `stop_call_ms`, `state_ms`, `total_ms`.
- **Stub defaults for manual runs** (env unset): state file `runs/.stub_state.json` (relative to cwd), time scale `0.1`, no detections. `stub.write_posture` creates the parent folder. An invalid state file raises `ValueError` (it surfaces as a `state_error` or `exception`).
- **Stub noise** (`GO2_STUB_NOISE=1`) fires on the first `get_sport_client`/`get_detector`/`sample_state` call. A faulted `Move` returns `STUB_ERR_INJECTED` even while sitting (fault checked first).
- **Real state mapping:** a list field (`position`, `velocity`, `imu_rpy`, `foot_force`) becomes `null` as a whole if any element is non-finite or the length is wrong, because `RobotState` lists cannot hold `null` elements. `RealDetector` checks that the weights file exists before touching the camera.
- **Test helper `tests/helpers/skills.py`:** `stub_env(tmp_path, detections=None, fault=None, **extra)`, `run_module(name, params, env)`, `single_response(proc)`.

## T3 — The five skills

- **Shared helper `go2_skills/motion.py`:** `require_enum`, `require_number` (parameter checks raising `InvalidParams`), `move_loop(vx, vy, vyaw, duration_s, period_s)` (the 10 Hz loop + `StopMove`, used by walk and turn) and `single_action(call, settle_s)` (used by sit and stretch).
- **No defaults applied by skills:** skills expect filled params (§7.3); a missing `distance_m` / `angle_deg` is `invalid_params`, like a missing `direction`. Numbers must be finite and not booleans; ints are accepted. Unknown extra keys are ignored. Enum values must match exactly (the dispatcher normalises case); only `detect_object.target` is stripped and lowercased, per §8.5.
- **`sdk_ret` on a failed `Move`** is the failing call's code (not the cleanup `StopMove`'s 0). If the cleanup `StopMove` also fails, the message becomes `"Move returned X; StopMove returned Y"`.
- **`duration_s`** = Move commands actually sent × `CMD_PERIOD_S`. On a full run this equals `n × CMD_PERIOD_S` (§8.1); after an `sdk_error` or an orphan break it shows what was really commanded.
- **Orphan break** (`orphaned: true`) still reports `status=ok` if the final `StopMove` returns 0 (the dispatcher that would read it is gone anyway).
- **Exceptions inside the motion loop** trigger a best-effort `StopMove()` before the exception propagates to `run_skill` (`code=exception`).
- **Settle waits** run only after a successful `StandDown`/`Stretch`; `exec_ms` includes them.
- **`SDK_TIMEOUT_S`** in walk/turn is documentary: the timeout is applied by `real.get_sport_client()` (`real.SPORT_CLIENT_TIMEOUT_S = 10.0`), since `backend.get_sport_client()` takes no arguments (§9).
- **`detect_object` errors:** any `DetectorError` maps to its `.code`, message `"<Type>: <msg>"`. Observations on error contain only `target`. `unsupported_object` still samples state (only the detector is skipped).

## T4 — Models and registry

- **Models:** every §6 model uses `extra="forbid"` via a private `_Model` base (only `RobotState` allows extras).
- **`catalog_text()` has no trailing newline**; `tests/golden/catalog.txt` is byte-identical (no final newline). The registry hash separator `"\n"` then joins the parts cleanly.
- **`registry_hash(system_text, catalog_text, tool_schema) -> str`** is a module-level function in `registry.py`; callers pass `prompts`/`llm.plan_tool_schema(horizon)` output.
- **Frontmatter:** the first line must be exactly `---` (trailing whitespace tolerated), closed by the next `---` line; empty, non-mapping or invalid YAML is a `RegistryError`. `params` must be a mapping when present (`params:` with no value is an error).
- **Stricter-than-stated checks** (spec silent): parameter names must match `^[a-z][a-z0-9_]*$`; `description` (skill and param) must be a non-empty string, and the skill description a single line (the catalog is line-based); enum `values` must be unique and already stripped; `min`/`max` must be finite numbers (not booleans); `unit` must be a non-empty string; `min`/`max`/`unit` on a non-numeric type are errors (like `values` on a non-enum).
- **Default checks:** `number` = finite int/float, not bool; `integer` = int, not bool (YAML `2.0` rejected — no conversion for declared defaults); `string` = non-empty after strip; `enum` = exactly one of `values`; numeric defaults within `min`/`max`. `default: null` is invalid.
- **`POLICY` must be a `SkillPolicy` instance** (besides existing and having the right `name`). Any exception during the entrypoint import is reported as "not importable".
- **No-default sentinel:** `registry.MISSING` (falsy singleton); `ParamSpec.required` property is `default is MISSING`.
- **Duplicate names** cannot arise from distinct folders (name must equal folder name), but the check exists; its test monkeypatches the per-folder loader.
- **Missing `skills_dir`** is a `RegistryError` naming the directory, as is zero skills.
- **Test entrypoint modules** live in `tests/helpers/skill_modules/` (importable as `helpers.skill_modules.*`, no `sys.path` changes).
