"""Skill registry: SKILL.md loading and validation, catalog rendering, registry hash (§10, §7.1)."""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .models import RegistryError
from .policies import SkillPolicy

SKILL_FILE = "SKILL.md"
FRONTMATTER_DELIMITER = "---"
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
REGISTRY_HASH_LEN = 16

FRONTMATTER_KEYS = frozenset({"name", "entrypoint", "description", "params", "expect"})
REQUIRED_KEYS = ("name", "entrypoint", "description")
PARAM_TYPES = frozenset({"number", "integer", "string", "enum"})
NUMERIC_TYPES = frozenset({"number", "integer"})
PARAM_KEYS = frozenset({"type", "description", "values", "min", "max", "default", "unit"})


class _Missing:
    """Sentinel for a ParamSpec without a default (the param is required)."""

    _instance: "_Missing | None" = None

    def __new__(cls) -> "_Missing":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "MISSING"

    def __bool__(self) -> bool:
        return False


MISSING: Any = _Missing()


@dataclass(frozen=True)
class ParamSpec:
    type: str
    description: str
    values: tuple[str, ...] | None = None
    min: float | None = None
    max: float | None = None
    default: Any = MISSING
    unit: str | None = None

    @property
    def required(self) -> bool:
        return self.default is MISSING


@dataclass(frozen=True)
class SkillDescriptor:
    name: str
    entrypoint: str
    description: str
    params: dict[str, ParamSpec]      # frontmatter order
    policy: SkillPolicy


# --- validation helpers ----------------------------------------------------------


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _is_nonempty_str(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _fmt_num(v: float) -> str:
    return f"{v:g}"


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


def _parse_param(path: Path, pname: Any, raw: Any) -> ParamSpec:
    if not isinstance(pname, str) or not NAME_RE.match(pname):
        raise RegistryError(f"{path}: parameter name {pname!r} must match {NAME_RE.pattern}")
    where = f"{path}: parameter '{pname}'"
    if not isinstance(raw, dict):
        raise RegistryError(f"{where}: spec must be a mapping")
    unknown = sorted(str(k) for k in raw if k not in PARAM_KEYS)
    if unknown:
        raise RegistryError(f"{where}: unknown key '{unknown[0]}'")
    for key in ("type", "description"):
        if key not in raw:
            raise RegistryError(f"{where}: missing required key '{key}'")

    ptype = raw["type"]
    if ptype not in PARAM_TYPES:
        raise RegistryError(f"{where}: type must be one of {', '.join(sorted(PARAM_TYPES))}, got {ptype!r}")
    if not _is_nonempty_str(raw["description"]):
        raise RegistryError(f"{where}: description must be a non-empty string")

    values: tuple[str, ...] | None = None
    if ptype == "enum":
        if "values" not in raw:
            raise RegistryError(f"{where}: enum requires 'values'")
        vals = raw["values"]
        if (not isinstance(vals, list) or not vals
                or not all(isinstance(v, str) and v and v == v.strip().lower() for v in vals)):
            raise RegistryError(f"{where}: values must be a non-empty list of lowercase strings")
        if len(set(vals)) != len(vals):
            raise RegistryError(f"{where}: values must be unique")
        values = tuple(vals)
    elif "values" in raw:
        raise RegistryError(f"{where}: 'values' is only allowed for type enum")

    bounds: dict[str, float | None] = {"min": None, "max": None}
    for key in ("min", "max", "unit"):
        if key in raw and ptype not in NUMERIC_TYPES:
            raise RegistryError(f"{where}: '{key}' is only allowed for type number or integer")
    for key in ("min", "max"):
        if key in raw:
            if not _is_number(raw[key]):
                raise RegistryError(f"{where}: {key} must be a finite number")
            bounds[key] = raw[key]
    lo, hi = bounds["min"], bounds["max"]
    if lo is not None and hi is not None and lo > hi:
        raise RegistryError(f"{where}: min must be <= max")

    unit = raw.get("unit")
    if unit is not None and not _is_nonempty_str(unit):
        raise RegistryError(f"{where}: unit must be a non-empty string")

    default = raw.get("default", MISSING)
    if default is not MISSING:
        _check_default(where, ptype, default, values, lo, hi)

    return ParamSpec(type=ptype, description=raw["description"], values=values,
                     min=lo, max=hi, default=default, unit=unit)


def _check_default(where: str, ptype: str, default: Any, values: tuple[str, ...] | None,
                   lo: float | None, hi: float | None) -> None:
    if ptype == "number":
        ok = _is_number(default)
    elif ptype == "integer":
        ok = isinstance(default, int) and not isinstance(default, bool)
    elif ptype == "string":
        ok = _is_nonempty_str(default)
    else:  # enum
        ok = isinstance(default, str) and values is not None and default in values
    if not ok:
        raise RegistryError(f"{where}: default {default!r} is not a valid {ptype}")
    if ptype in NUMERIC_TYPES:
        if (lo is not None and default < lo) or (hi is not None and default > hi):
            raise RegistryError(f"{where}: default {default!r} is outside its min/max")


def _load_skill(folder: Path) -> SkillDescriptor:
    path = folder / SKILL_FILE
    data = _read_frontmatter(path)

    unknown = sorted(str(k) for k in data if k not in FRONTMATTER_KEYS)
    if unknown:
        raise RegistryError(f"{path}: unknown key '{unknown[0]}'")
    for key in REQUIRED_KEYS:
        if key not in data:
            raise RegistryError(f"{path}: missing required key '{key}'")

    name = data["name"]
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise RegistryError(f"{path}: name {name!r} must match {NAME_RE.pattern}")
    if name != folder.name:
        raise RegistryError(f"{path}: name '{name}' does not match folder name '{folder.name}'")
    entrypoint = data["entrypoint"]
    if not _is_nonempty_str(entrypoint):
        raise RegistryError(f"{path}: entrypoint must be a non-empty string")
    description = data["description"]
    if not _is_nonempty_str(description) or "\n" in description.strip():
        raise RegistryError(f"{path}: description must be a non-empty single-line string")

    raw_params = data.get("params", {})
    if not isinstance(raw_params, dict):
        raise RegistryError(f"{path}: params must be a mapping")
    params = {pname: _parse_param(path, pname, spec) for pname, spec in raw_params.items()}

    try:
        module = importlib.import_module(entrypoint)
    except Exception as e:  # any import failure makes the skill unusable
        raise RegistryError(f"{path}: entrypoint '{entrypoint}' is not importable: "
                            f"{type(e).__name__}: {e}") from None
    policy = getattr(module, "POLICY", None)
    if policy is None:
        raise RegistryError(f"{path}: module '{entrypoint}' has no POLICY")
    if not isinstance(policy, SkillPolicy):
        raise RegistryError(f"{path}: {entrypoint}.POLICY is not a SkillPolicy")
    if policy.name != name:
        raise RegistryError(f"{path}: POLICY.name '{policy.name}' does not match name '{name}'")

    return SkillDescriptor(name=name, entrypoint=entrypoint, description=description.strip(),
                           params=params, policy=policy)


# --- catalog ---------------------------------------------------------------------


def _type_phrase(spec: ParamSpec) -> str:
    if spec.type == "enum":
        return "one of " + ", ".join(spec.values or ())
    if spec.type == "string":
        return "text"
    word = spec.type  # number / integer
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
    def load(cls, skills_dir: Path) -> "Registry":
        """Load every ``<skills_dir>/<name>/SKILL.md``; raises RegistryError."""
        skills_dir = Path(skills_dir)
        if not skills_dir.is_dir():
            raise RegistryError(f"{skills_dir}: skills directory not found")
        skills: dict[str, SkillDescriptor] = {}
        for folder in sorted(skills_dir.iterdir()):
            if not folder.is_dir() or not (folder / SKILL_FILE).is_file():
                continue
            desc = _load_skill(folder)
            if desc.name in skills:
                raise RegistryError(f"{folder / SKILL_FILE}: duplicate skill name '{desc.name}'")
            skills[desc.name] = desc
        if not skills:
            raise RegistryError(f"{skills_dir}: no skills found (no */{SKILL_FILE})")
        return cls(skills)

    def get(self, name: str) -> SkillDescriptor | None:
        return self._skills.get(name)

    def names(self) -> list[str]:
        return sorted(self._skills)

    def catalog_text(self) -> str:
        """Deterministic catalog (§10): skills by name, one line per param, no trailing newline."""
        lines: list[str] = []
        for name in self.names():
            lines.extend(_render_skill(self._skills[name]))
        return "\n".join(lines)


def registry_hash(system_text: str, catalog_text: str, tool_schema: dict) -> str:
    """First 16 hex chars of sha256 over the prompt surface (§10)."""
    blob = system_text + "\n" + catalog_text + "\n" + json.dumps(tool_schema, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:REGISTRY_HASH_LEN]
