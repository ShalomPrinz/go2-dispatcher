"""The v1 catalog's SKILL.md bodies stay in sync with the code constants they quote (contract/docs/contract.md)."""

from __future__ import annotations

import re
from pathlib import Path

from contract.manifest import parse_skill_file
from skills import turn, walk

CATALOG = Path(__file__).resolve().parents[2] / "catalog"


def _number_before(text: str, unit: str) -> float:
    m = re.search(rf"(\d+(?:\.\d+)?) {re.escape(unit)}", text)
    assert m, f"no '<number> {unit}' in {text!r}"
    return float(m.group(1))


def test_prose_numbers_match_code_constants():
    def effect(name: str) -> str:
        sf = parse_skill_file(CATALOG / name / "SKILL.md")
        return next(s.text for s in sf.sections if s.heading == "Effect")

    assert _number_before(effect("walk"), "m/s") == walk.VELOCITY_MPS
    assert _number_before(effect("turn"), "rad/s") == turn.YAW_RATE_RPS
