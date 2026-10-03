"""Configuration: models, validation, base dir and path resolution, .env parser (docs/configuration.md)."""

from __future__ import annotations

import copy
import os
import re
import sys
from pathlib import Path
from typing import Annotated, Any, Literal, NoReturn

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PrivateAttr,
    StrictInt,
    StrictStr,
    ValidationError,
    field_validator,
    model_validator,
)

from skills.coco import COCO_CLASSES

from .models import ConfigError

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

DEFAULT_CONFIG_PATH = Path("config.toml")
ENV_FILE_NAME = ".env"
DETECTION_VALUE_RE = re.compile(r"^(left|center|right):(near|medium|far)$")


def _strict_number(v: Any) -> Any:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError(f"must be a number, got {v!r}")
    return v


# Floats in config: int or float only (no bool, no numeric strings), finite; stored as float.
FiniteFloat = Annotated[float, BeforeValidator(_strict_number), Field(allow_inf_nan=False)]


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunConfig(_Section):
    condition: StrictStr = ""


class LLMConfig(_Section):
    model: StrictStr = "claude-sonnet-5-5"  # (tunable)
    max_tokens: StrictInt = Field(default=2048, ge=1)
    thinking: Literal["between_tools", "adaptive"] = "between_tools"
    request_timeout_s: FiniteFloat = Field(default=60.0, gt=0)  # (tunable)
    infra_backoff_s: list[FiniteFloat] = [1.0, 4.0]  # one sleep per retry; length = retry count

    @field_validator("infra_backoff_s")
    @classmethod
    def _backoff_non_negative(cls, v: list[float]) -> list[float]:
        for x in v:
            if x < 0:
                raise ValueError("every value must be >= 0")
        return v


class LoopConfig(_Section):
    planning_horizon: StrictInt = Field(default=5, ge=1)  # (tunable)
    max_failures: StrictInt = Field(default=3, ge=1)  # (tunable)
    max_llm_calls: StrictInt = Field(default=20, ge=1)  # (tunable)
    task_time_limit_s: FiniteFloat = Field(default=300.0, gt=0)  # (tunable)
    context_history_k: StrictInt = Field(default=10, ge=1)  # (tunable)


class MotionBudgetConfig(_Section):
    max_distance_m: FiniteFloat = Field(default=10.0, ge=0)  # (tunable)
    max_rotation_deg: FiniteFloat = Field(default=720.0, ge=0)  # (tunable)


class SkillsConfig(_Section):
    dir: Path = Path("skills/catalog")


class RobotConfig(_Section):
    backend: Literal["stub", "real"] = "stub"
    network_interface: StrictStr = ""
    yolo_weights: Path = Path("models/yolov8n.pt")
    stop_move_timeout_s: FiniteFloat = Field(default=10.0, gt=0)
    read_state_timeout_s: FiniteFloat = Field(default=10.0, gt=0)


class FaultConfig(_Section):
    step: StrictInt = Field(ge=1)
    kind: Literal["error", "hang", "crash", "garbage"]


class StubConfig(_Section):
    time_scale: FiniteFloat = Field(default=0.1, gt=0)
    initial_posture: Literal["standing", "sitting"] = "standing"
    state_file: Path = Path("runs/.stub_state.json")
    detections: dict[str, StrictStr] = {}
    faults: list[FaultConfig] = []

    @field_validator("detections")
    @classmethod
    def _check_detections(cls, v: dict[str, str]) -> dict[str, str]:
        for name, where in v.items():
            if name not in COCO_CLASSES:
                raise ValueError(f"'{name}' is not a COCO class")
            if not DETECTION_VALUE_RE.match(where):
                raise ValueError(f"value for '{name}' must match {DETECTION_VALUE_RE.pattern}, got '{where}'")
        return v

    @field_validator("faults")
    @classmethod
    def _unique_fault_steps(cls, v: list[FaultConfig]) -> list[FaultConfig]:
        steps = [f.step for f in v]
        if len(steps) != len(set(steps)):
            raise ValueError("fault steps must be unique")
        return v


class LogConfig(_Section):
    dir: Path = Path("runs")


class TelegramConfig(_Section):
    allowed_user_ids: list[StrictInt] = []


class Config(_Section):
    run: RunConfig = RunConfig()
    llm: LLMConfig = LLMConfig()
    loop: LoopConfig = LoopConfig()
    motion_budget: MotionBudgetConfig = MotionBudgetConfig()
    skills: SkillsConfig = SkillsConfig()
    robot: RobotConfig = RobotConfig()
    stub: StubConfig = StubConfig()
    log: LogConfig = LogConfig()
    telegram: TelegramConfig = TelegramConfig()

    _base_dir: Path = PrivateAttr(default_factory=Path.cwd)

    @property
    def base_dir(self) -> Path:
        """Folder relative paths were resolved against; subprocess cwd (docs/configuration.md)."""
        return self._base_dir

    @model_validator(mode="after")
    def _real_backend_rules(self) -> Config:
        if self.robot.backend == "real":
            if not self.robot.network_interface.strip():
                raise ValueError('robot.network_interface is required when robot.backend = "real"')
            if self.stub.faults:
                raise ValueError('stub.faults must be empty when robot.backend = "real"')
        return self


# --- building -----------------------------------------------------------------


def _format_validation_error(err: ValidationError) -> str:
    parts = []
    for e in err.errors():
        loc = ".".join(str(p) for p in e["loc"])
        msg = e["msg"]
        if e["type"] == "extra_forbidden":
            msg = "unknown key"
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts)


def apply_overrides(data: dict[str, Any], overrides: dict[str, Any] | None) -> dict[str, Any]:
    """Return a copy of ``data`` with dotted-key overrides applied, e.g. {"robot.backend": "real"}."""
    out = copy.deepcopy(data)
    for dotted, value in (overrides or {}).items():
        *sections, key = dotted.split(".")
        node = out
        for s in sections:
            child = node.get(s)
            if not isinstance(child, dict):
                child = {}
                node[s] = child
            node = child
        node[key] = value
    return out


def _resolve(p: Path, base_dir: Path) -> Path:
    p = p.expanduser()
    return p if p.is_absolute() else (base_dir / p)


def build_config(data: dict[str, Any], base_dir: Path, overrides: dict[str, Any] | None = None) -> Config:
    """Validate raw config data, resolve relative paths against ``base_dir``, create ``log.dir``."""
    base_dir = Path(base_dir).resolve()
    try:
        cfg = Config.model_validate(apply_overrides(data, overrides))
    except ValidationError as e:
        raise ConfigError(_format_validation_error(e)) from None
    cfg.skills.dir = _resolve(cfg.skills.dir, base_dir)
    cfg.log.dir = _resolve(cfg.log.dir, base_dir)
    cfg.stub.state_file = _resolve(cfg.stub.state_file, base_dir)
    cfg.robot.yolo_weights = _resolve(cfg.robot.yolo_weights, base_dir)
    cfg._base_dir = base_dir
    try:
        cfg.log.dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise ConfigError(f"cannot create log.dir {cfg.log.dir}: {e}") from None
    return cfg


def load_config(config_path: Path | str | None = None, overrides: dict[str, Any] | None = None) -> Config:
    """Load config (docs/configuration.md). ``None`` means the default ``./config.toml``; if that is
    missing, defaults are used with a one-line stderr warning. An explicit path
    that is missing raises ConfigError."""
    if config_path is None:
        path = DEFAULT_CONFIG_PATH
        if not path.is_file():
            print(
                f"Warning: {path} not found; using default configuration.",
                file=sys.stderr,
            )
            return build_config({}, Path.cwd(), overrides)
    else:
        path = Path(config_path)
        if not path.is_file():
            raise ConfigError(f"config file not found: {path}")
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: invalid TOML: {e}") from None
    except OSError as e:
        raise ConfigError(f"cannot read {path}: {e}") from None
    return build_config(data, path.resolve().parent, overrides)


# --- .env ---------------------------------------------------------------------


def parse_env_text(text: str) -> dict[str, str]:
    """Parse .env text: KEY=VALUE per line, optional ``export ``, ``#`` comments and
    blank lines ignored, one pair of matching surrounding quotes stripped, no
    interpolation. Lines without ``=`` are ignored."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        key = key.strip()
        if not sep or not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        out[key] = value
    return out


def parse_env_file(path: Path) -> dict[str, str]:
    return parse_env_text(Path(path).read_text(encoding="utf-8"))


def load_env_file(base_dir: Path) -> dict[str, str]:
    """Load ``{base_dir}/.env`` into ``os.environ``; real environment variables win.
    Returns the pairs that were applied. A missing file is not an error."""
    path = Path(base_dir) / ENV_FILE_NAME
    if not path.is_file():
        return {}
    applied: dict[str, str] = {}
    for k, v in parse_env_file(path).items():
        if k not in os.environ:
            os.environ[k] = v
            applied[k] = v
    return applied


def config_error_exit(message: str) -> NoReturn:
    print(f"Config error: {message}", file=sys.stderr)
    raise SystemExit(2)


def load_config_and_env(config_path: Path | str | None, overrides: dict[str, Any] | None = None) -> Config:
    """Load config and the base-dir ``.env``. Config errors print
    ``Config error: <message>`` to stderr and exit with code 2 (docs/configuration.md)."""
    try:
        cfg = load_config(config_path, overrides)
    except ConfigError as e:
        config_error_exit(str(e))
    load_env_file(cfg.base_dir)
    return cfg
