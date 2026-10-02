"""SKILL.md frontmatter schema and parser (skills/docs/skills.md).

Imported only by the dispatcher process; skill entry points must not import it (it pulls in pydantic and yaml).
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

SKILL_FILE = "SKILL.md"
FRONTMATTER_DELIMITER = "---"
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

Name = Annotated[str, StringConstraints(pattern=NAME_RE.pattern)]
NonEmptyStr = Annotated[str, StringConstraints(pattern=r"\S")]


class SkillFileError(ValueError):
    """A SKILL.md that cannot be read or fails the schema; the message starts with the file path."""


class _Frontmatter(BaseModel):
    # strict: no coercion; extra keys are rejected (skills/docs/skills.md)
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class ParamSpec(_Frontmatter):
    """One entry under ``params:``. ``required`` is true when the file gives no ``default``."""

    type: Literal["number", "string", "enum"]
    description: NonEmptyStr
    values: tuple[str, ...] | None = Field(None, strict=False)  # YAML gives a list
    min: float | None = Field(None, allow_inf_nan=False)
    max: float | None = Field(None, allow_inf_nan=False)
    default: Any = None
    unit: NonEmptyStr | None = None

    @property
    def required(self) -> bool:
        return "default" not in self.model_fields_set

    @field_validator("values", "min", "max", "unit", mode="before")
    @classmethod
    def _no_null(cls, v: Any, info: ValidationInfo) -> Any:
        # an optional key, when present, must carry a value; a YAML set is not an ordered list
        if v is None:
            raise ValueError(f"'{info.field_name}' must not be null")
        if info.field_name == "values" and not isinstance(v, (list, tuple)):
            raise ValueError("values must be a list")
        return v

    @model_validator(mode="after")
    def _check(self) -> ParamSpec:
        if self.type == "enum":
            vals = self.values
            if vals is None:
                raise ValueError("enum requires 'values'")
            if not vals or not all(v and v == v.strip().lower() for v in vals):
                raise ValueError("values must be a non-empty list of lowercase strings")
            if len(set(vals)) != len(vals):
                raise ValueError("values must be unique")
        elif self.values is not None:
            raise ValueError("'values' is only allowed for type enum")
        if self.type != "number":
            for key in ("min", "max", "unit"):
                if getattr(self, key) is not None:
                    raise ValueError(f"'{key}' is only allowed for type number")
        lo, hi = self.min, self.max
        if lo is not None and hi is not None and lo > hi:
            raise ValueError("min must be <= max")
        if not self.required:
            self._check_default(lo, hi)
        return self

    def _check_default(self, lo: float | None, hi: float | None) -> None:
        d = self.default
        if self.type == "number":
            ok = _is_number(d)
        elif self.type == "string":
            ok = _is_nonempty_str(d)
        else:  # enum
            ok = isinstance(d, str) and d in (self.values or ())
        if not ok:
            raise ValueError(f"default {d!r} is not a valid {self.type}")
        if self.type == "number" and ((lo is not None and d < lo) or (hi is not None and d > hi)):
            raise ValueError(f"default {d!r} is outside its min/max")


class SkillFrontmatter(_Frontmatter):
    name: Name
    entrypoint: NonEmptyStr
    description: NonEmptyStr
    params: dict[Name, ParamSpec] = {}  # frontmatter order
    expect: Any = None  # accepted and ignored (docs/roadmap.md)

    @field_validator("description")
    @classmethod
    def _single_line(cls, v: str) -> str:
        if "\n" in v.strip():
            raise ValueError("description must be a single line")
        return v


def parse_skill_file(path: Path) -> SkillFrontmatter:
    """Read and validate one SKILL.md's frontmatter; raises SkillFileError."""
    try:
        return SkillFrontmatter.model_validate(_read_frontmatter(path))
    except ValidationError as e:
        raise SkillFileError(f"{path}: {_format_validation_error(e)}") from None


# --- helpers ---------------------------------------------------------------------


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _is_nonempty_str(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _format_validation_error(err: ValidationError) -> str:
    parts = []
    for e in err.errors():
        loc = ".".join(str(p) for p in e["loc"])
        msg = "unknown key" if e["type"] == "extra_forbidden" else e["msg"]
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts)


def _read_frontmatter(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise SkillFileError(f"{path}: cannot read: {e}") from None
    lines = text.splitlines()
    if not lines or lines[0].rstrip() != FRONTMATTER_DELIMITER:
        raise SkillFileError(f"{path}: missing frontmatter (first line must be '---')")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].rstrip() == FRONTMATTER_DELIMITER)
    except StopIteration:
        raise SkillFileError(f"{path}: frontmatter is not closed by a '---' line") from None
    try:
        data = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as e:
        raise SkillFileError(f"{path}: invalid frontmatter YAML: {e}") from None
    if not isinstance(data, dict):
        raise SkillFileError(f"{path}: frontmatter must be a mapping")
    return data
