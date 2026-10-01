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
