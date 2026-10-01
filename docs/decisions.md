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
