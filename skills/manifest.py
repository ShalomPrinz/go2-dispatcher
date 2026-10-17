"""SKILL.md schema and parser: frontmatter and the body's section template (skills/docs/skills.md).

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
SECTION_PREFIX = "## "
SECTION_HEADINGS = ("Use when", "Requires", "Effect", "Limits")  # every one required, in this order
BODY_MAX_CHARS = 800  # (tunable)
_HEADING_RE = re.compile(r"^#{1,6}(\s|$)")

Name = Annotated[str, StringConstraints(pattern=NAME_RE.pattern)]
NonEmptyStr = Annotated[str, StringConstraints(pattern=r"\S")]


class SkillFileError(ValueError):
    """A SKILL.md that cannot be read or fails the schema; the message starts with the file path."""


class _Strict(BaseModel):
    # strict: no coercion; extra keys are rejected (skills/docs/skills.md)
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class ParamSpec(_Strict):
    """One entry under ``params:``. ``required`` is true when the file gives no ``default``."""

    type: Literal["number", "string", "enum"]
    description: NonEmptyStr
    values: tuple[str, ...] | None = Field(default=None, strict=False)  # YAML gives a list
    min: float | None = Field(default=None, allow_inf_nan=False)
    max: float | None = Field(default=None, allow_inf_nan=False)
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
            if ok and ((lo is not None and d < lo) or (hi is not None and d > hi)):
                raise ValueError(f"default {d!r} is outside its min/max")
        elif self.type == "string":
            ok = _is_nonempty_str(d)
        else:  # enum
            ok = isinstance(d, str) and d in (self.values or ())
        if not ok:
            raise ValueError(f"default {d!r} is not a valid {self.type}")


class SkillFrontmatter(_Strict):
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


class SkillSection(_Strict):
    """One body section: ``heading`` without the ``## `` prefix, ``text`` stripped and otherwise verbatim."""

    heading: str
    text: str


class SkillFile(SkillFrontmatter):
    """A parsed SKILL.md: the frontmatter keys plus the body ``sections``, one per heading in template order."""

    sections: tuple[SkillSection, ...]


def parse_skill_file(path: Path) -> SkillFile:
    """Read and validate one SKILL.md, frontmatter then body; raises SkillFileError."""
    data, body = _read_skill_file(path)
    try:
        fm = SkillFrontmatter.model_validate(data)  # rejects a 'sections' key like any unknown key
    except ValidationError as e:
        raise SkillFileError(f"{path}: {_format_validation_error(e)}") from None
    try:
        sections = _parse_body(body)
    except ValueError as e:
        raise SkillFileError(f"{path}: body: {e}") from None
    # fields_set keeps a param's missing default (required) and the class defaults
    keys = {k: getattr(fm, k) for k in fm.model_fields_set}
    return SkillFile.model_validate({**keys, "sections": sections})


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


def _parse_body(lines: list[str]) -> tuple[SkillSection, ...]:
    """Split the body into the template sections; raises ValueError naming the first rule broken."""
    size = len("\n".join(lines).strip())
    if size > BODY_MAX_CHARS:
        raise ValueError(f"{size} characters, over the cap of {BODY_MAX_CHARS}")
    found: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in lines:
        if not _HEADING_RE.match(line):
            if current is not None:
                current.append(line)
            elif line.strip():
                raise ValueError(f"text outside a section: '{line.strip()}'")
            continue
        heading = line[len(SECTION_PREFIX) :].strip() if line.startswith(SECTION_PREFIX) else None
        if heading not in SECTION_HEADINGS:
            allowed = ", ".join(f"'{SECTION_PREFIX}{h}'" for h in SECTION_HEADINGS)
            raise ValueError(f"unknown heading '{line.strip()}'; allowed: {allowed}")
        index = SECTION_HEADINGS.index(heading)
        if index < len(found):
            raise ValueError(f"heading '{SECTION_PREFIX}{heading}' is repeated or out of order")
        if index > len(found):
            raise ValueError(f"missing section '{SECTION_PREFIX}{SECTION_HEADINGS[len(found)]}'")
        current = found[heading] = []
    if len(found) < len(SECTION_HEADINGS):
        raise ValueError(f"missing section '{SECTION_PREFIX}{SECTION_HEADINGS[len(found)]}'")
    sections = tuple(SkillSection(heading=h, text="\n".join(found[h]).strip()) for h in SECTION_HEADINGS)
    for s in sections:
        if not s.text:
            raise ValueError(f"section '{SECTION_PREFIX}{s.heading}' is empty; write 'None.' if nothing applies")
    return sections


def _read_skill_file(path: Path) -> tuple[dict[str, Any], list[str]]:
    """The frontmatter mapping and the body lines after the closing delimiter."""
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
    return data, lines[end + 1 :]
