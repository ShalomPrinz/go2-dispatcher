"""Skill registry: discovery, entrypoint and POLICY checks, catalog rendering, registry hash (skills/docs/skills.md).

SKILL.md frontmatter parsing and validation live in skills/frontmatter.py.
"""

from __future__ import annotations

import hashlib
import importlib
import json
from dataclasses import dataclass
from pathlib import Path

from skills.frontmatter import SKILL_FILE, ParamSpec, SkillFileError, parse_skill_file
from skills.result import SkillPolicy

from .models import RegistryError

REGISTRY_HASH_LEN = 16


@dataclass(frozen=True)
class SkillDescriptor:
    name: str
    entrypoint: str
    description: str
    params: dict[str, ParamSpec]  # frontmatter order
    policy: SkillPolicy


# --- loading ---------------------------------------------------------------------


def _load_skill(folder: Path) -> SkillDescriptor:
    path = folder / SKILL_FILE
    try:
        fm = parse_skill_file(path)
    except SkillFileError as e:  # same message; transports handle only RegistryError
        raise RegistryError(str(e)) from None
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


def _fmt_num(v: float) -> str:
    return f"{v:g}"


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


def _fmt_default(spec: ParamSpec) -> str:
    return _fmt_num(spec.default) if spec.type == "number" else str(spec.default)


def _render_skill(d: SkillDescriptor) -> list[str]:
    lines = [f"{d.name}: {d.description}"]
    if not d.params:
        lines.append("  - no parameters")
    for pname, spec in d.params.items():
        req = "required" if spec.required else f"optional, default {_fmt_default(spec)}"
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
