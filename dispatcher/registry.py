"""Skill registry: SKILL.md loading and validation, catalog rendering, registry hash (skills/docs/skills.md)."""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import re
from dataclasses import dataclass
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

from skills.result import SkillPolicy

from .models import RegistryError

SKILL_FILE = "SKILL.md"
FRONTMATTER_DELIMITER = "---"
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
REGISTRY_HASH_LEN = 16

Name = Annotated[str, StringConstraints(pattern=NAME_RE.pattern)]
NonEmptyStr = Annotated[str, StringConstraints(pattern=r"\S")]


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


@dataclass(frozen=True)
class SkillDescriptor:
    name: str
    entrypoint: str
    description: str
    params: dict[str, ParamSpec]  # frontmatter order
    policy: SkillPolicy


# --- validation helpers ----------------------------------------------------------


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _is_nonempty_str(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _fmt_num(v: float) -> str:
    return f"{v:g}"


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
        raise RegistryError(f"{path}: cannot read: {e}") from None
    lines = text.splitlines()
    if not lines or lines[0].rstrip() != FRONTMATTER_DELIMITER:
        raise RegistryError(f"{path}: missing frontmatter (first line must be '---')")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].rstrip() == FRONTMATTER_DELIMITER)
    except StopIteration:
        raise RegistryError(f"{path}: frontmatter is not closed by a '---' line") from None
    try:
        data = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as e:
        raise RegistryError(f"{path}: invalid frontmatter YAML: {e}") from None
    if not isinstance(data, dict):
        raise RegistryError(f"{path}: frontmatter must be a mapping")
    return data


def _load_skill(folder: Path) -> SkillDescriptor:
    path = folder / SKILL_FILE
    try:
        fm = SkillFrontmatter.model_validate(_read_frontmatter(path))
    except ValidationError as e:
        raise RegistryError(f"{path}: {_format_validation_error(e)}") from None
    name, entrypoint = fm.name, fm.entrypoint
    if name != folder.name:
        raise RegistryError(f"{path}: name '{name}' does not match folder name '{folder.name}'")

    try:
        module = importlib.import_module(entrypoint)
    except Exception as e:  # any import failure makes the skill unusable
        raise RegistryError(f"{path}: entrypoint '{entrypoint}' is not importable: {type(e).__name__}: {e}") from None
    policy = getattr(module, "POLICY", None)
    if policy is None:
        raise RegistryError(f"{path}: module '{entrypoint}' has no POLICY")
    if not isinstance(policy, SkillPolicy):
        raise RegistryError(f"{path}: {entrypoint}.POLICY is not a SkillPolicy")
    if policy.name != name:
        raise RegistryError(f"{path}: POLICY.name '{policy.name}' does not match name '{name}'")

    return SkillDescriptor(
        name=name, entrypoint=entrypoint, description=fm.description.strip(), params=dict(fm.params), policy=policy
    )


# --- catalog ---------------------------------------------------------------------


def _type_phrase(spec: ParamSpec) -> str:
    if spec.type == "enum":
        return "one of " + ", ".join(spec.values or ())
    if spec.type == "string":
        return "text"
    word = spec.type  # number
    if spec.min is not None and spec.max is not None:
        phrase = f"{word} from {_fmt_num(spec.min)} to {_fmt_num(spec.max)}"
    elif spec.min is not None:
        phrase = f"{word}, at least {_fmt_num(spec.min)}"
    elif spec.max is not None:
        phrase = f"{word}, at most {_fmt_num(spec.max)}"
    else:
        phrase = word
    return f"{phrase} {spec.unit}" if spec.unit else phrase


def _fmt_default(v: Any) -> str:
    return _fmt_num(v) if _is_number(v) else str(v)


def _render_skill(d: SkillDescriptor) -> list[str]:
    lines = [f"{d.name}: {d.description}"]
    if not d.params:
        lines.append("  - no parameters")
    for pname, spec in d.params.items():
        req = "required" if spec.required else f"optional, default {_fmt_default(spec.default)}"
        lines.append(f"  - {pname} ({req}): {_type_phrase(spec)}. {spec.description}")
    return lines


# --- registry --------------------------------------------------------------------


class Registry:
    def __init__(self, skills: dict[str, SkillDescriptor]):
        self._skills = dict(sorted(skills.items()))

    @classmethod
    def load(cls, skills_dir: Path) -> Registry:
        """Load every ``<skills_dir>/<name>/SKILL.md``; raises RegistryError."""
        skills_dir = Path(skills_dir)
        if not skills_dir.is_dir():
            raise RegistryError(f"{skills_dir}: skills directory not found")
        skills: dict[str, SkillDescriptor] = {}
        for folder in sorted(skills_dir.iterdir()):
            if not folder.is_dir() or not (folder / SKILL_FILE).is_file():
                continue
            desc = _load_skill(folder)  # name == folder name, so names are unique
            skills[desc.name] = desc
        if not skills:
            raise RegistryError(f"{skills_dir}: no skills found (no */{SKILL_FILE})")
        return cls(skills)

    def get(self, name: str) -> SkillDescriptor | None:
        return self._skills.get(name)

    def names(self) -> list[str]:
        return sorted(self._skills)

    def catalog_text(self) -> str:
        """Deterministic catalog (skills/docs/skills.md): skills by name, one line per param, no trailing newline."""
        lines: list[str] = []
        for name in self.names():
            lines.extend(_render_skill(self._skills[name]))
        return "\n".join(lines)


def registry_hash(system_text: str, catalog_text: str, tool_schema: dict) -> str:
    """First 16 hex chars of sha256 over the prompt surface (skills/docs/skills.md)."""
    blob = system_text + "\n" + catalog_text + "\n" + json.dumps(tool_schema, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:REGISTRY_HASH_LEN]
