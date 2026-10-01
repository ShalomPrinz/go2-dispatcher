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

## T5 — Bounds, precheck, motion budget

- **`precheck` and `PrecheckResult` live in `bounds.py`** (spec gives no module).
- **Violation order:** all unknown params (received order), then all missing required params (frontmatter order), then type violations (frontmatter order), then range violations; range is checked only for params whose type passed. An unknown skill returns only its one violation.
- **`{expected}` wording:** `a finite number`, `an integer`, `a non-empty string`, `one of a, b, c`. A non-integral float for `integer` is a type violation.
- **Filled params** are in frontmatter order (declared params only; defaults are copied as declared).
- **`cut_message(text, limit=200)`** in `bounds.py`: collapses whitespace to one line and cuts to 199 + `…` (same rule as §11.6). Used for the bounds and motion-budget `error_message`.
- **Motion-budget message** is defined in `budget.py` (`MOTION_BUDGET_MESSAGE`, `MotionBudget.exceeded_message(kind, cost)`); `{need}` is the step's cost in that kind, `{left}` is `max - used` (floored at 0), both rounded to 2 decimals. T6's `prompts.py` should re-export it rather than duplicate it.
- **`MotionBudget` extras:** `max_distance_m`/`max_rotation_deg` attributes and `remaining(kind)`. Tolerance `BUDGET_EPSILON = 1e-9`.
- **Rejected StepResults** keep `motion_cost` at zero (not dispatched, never charged), including `motion_budget_exceeded`.

## T6 — Prompts, renderer, context builder

- **Context input:** `context.ContextInput` (frozen dataclass, a view of §15.2) + `build_user_message(inp, registry)`; the schema retry message is `context.schema_retry_message(user, errors)`. The user message has no trailing newline; sections are joined by `"\n\n"`.
- **Extra prompt helpers:** `prompts.system_text(horizon)`, `system_blocks(horizon, catalog_text)` (`[S1, "## Skills\n" + catalog]`), `notice(reason, **args)`, `rejection_section(errors)`, `operator_message(outcome, *, stop_move_failed=False, **args)`, `help_text(backend)`. The INTERNAL_ERROR placeholder `{ExceptionType}` is named `{exception_type}`.
- **Rejection lines:** each validation error is collapsed to one line (whitespace runs → one space).
- **Failure notice** takes `max_failures` from the context input; `notice_args` holds `n`, `skill`, `outcome`, `f` as in §15.2.
- **Renderer:** a failure line with no `error_message` renders as `-> {outcome}` (no `: `). The renderer re-applies `bounds.cut_message` to `error_message` defensively. Observation values use the same value formatting as params. A step with `index is None` (or a non-dispatched outcome) always uses the `- rejected before running:` form, also when `numbered=False`. `ok` observations come from the registry's policy; an unknown skill shows none.
- **Remaining plan:** `(none)` when `remaining` is empty or `remaining_tag` is `None`.
- **Previous task Message** is inserted verbatim (not cut or collapsed), like the task text.

## T7 — Plan validation and LLM client

- **anthropic SDK 1.x runs on `httpx2`:** the locked `anthropic` (1.11.0) rejects `httpx.Client`, so `AnthropicPlanner(http_client=...)` takes an `httpx2.Client`, and the LLM client tests use `httpx2.MockTransport` / `httpx2.ConnectError`. `httpx2` is now a direct dependency in `pyproject.toml` (`uv.lock` updated); `httpx` stays (python-telegram-bot).
- **`temperature` via `extra_body`:** SDK 1.x removed the `temperature` keyword from `messages.create()`; §12.3 requires it in the request, so it is sent as `extra_body={"temperature": cfg.temperature}` (same JSON body).
- **Model compatibility warning (not changed):** per the bundled API docs, `claude-sonnet-5-5` (the `llm.model` default) returns 400 for forced `tool_choice` `{"type": "tool"}` and for non-default `temperature`. §12.3 mandates both, so the code follows the spec; a live run needs a model that accepts them (e.g. Sonnet 4.6 / Haiku 4.5) or a spec change. Unit tests are unaffected.
- **`LLMResult`, `PlannerClient`, `TOOL_REQUIRED`, `STEP_REQUIRED`, `TOOL_NAME`** live in `llm.py`. Constants: `RETRYABLE_STATUS_CODES = {408, 409, 429}` (plus >= 500), `RETRY_AFTER_CAP_S = 30.0`, `ERR_MAX_TOKENS`, `ERR_NO_TOOL_CALL`.
- **Pre-attempt checks order:** `remaining_s() <= 0` → `LLMInterrupted("task_time_limit")` is checked before `stop_event` → `LLMInterrupted("operator")` (the order §12.3 lists them).
- **Retryable classification:** any other `anthropic.APIError` (e.g. `APIResponseValidationError`) is non-retryable → `LLMUnavailable`. A `retry-after` header is used only if it parses as a finite float >= 0.
- **`on_infra_retry` record:** `attempt` is the 1-based number of the attempt that failed; `status_code` is `None` for connection errors; `attempt_latency_ms` is monotonic time of the failed attempt.
- **`max_tokens` reply:** `tool_input` is still filled from the first `submit_plan` block if present (logged), but it is not validated.
- **Validation details:** a pydantic error with an empty `loc` (non-dict input) is reported as `input: {msg}` (`validation.ROOT_LOC`). The `replan_after` range rule is only checked for status PLAN (with DONE/ABORT only the "only allowed with status PLAN" error is reported). When the horizon is exceeded, schema and semantic checks still run and their errors follow the horizon error.
- **`ScriptedPlanner`** (`tests/helpers/planner.py`, exported from `helpers`): `Plan` items are returned as valid without re-validation; `dict` items are validated with the horizon read from the `tool_schema` kwarg (`maxItems`). `on_call(call_index)` runs before returning or raising. Extras: `.calls` (list of kwargs dicts), `.remaining`, `SCRIPTED_USAGE`. Synthetic `content` is one `tool_use` block; `stop_reason="tool_use"`, `latency_ms=total_ms=0`, `response_id=f"msg_scripted_{n}"`, `request_id=None`.
