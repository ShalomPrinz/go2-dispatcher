"""The skill-side SkillResponse: one-line error messages and the error-iff-status rule
(skills/docs/skills.md). Validation by the dispatcher is checked in
tests/integration/test_skill_response.py."""

from __future__ import annotations

import pytest

from skills import result
from skills.result import build_response
from skills.schema import SkillError, SkillResponse


def test_message_collapsed_and_cut():
    msg = "line one\nline   two\r\n\tthree " + "x" * 400
    out = build_response("walk", "error", error_code="exception", error_message=msg).error.message
    assert "\n" not in out and "\r" not in out and "\t" not in out
    assert out.startswith("line one line two three x")
    assert len(out) == result.ERROR_MESSAGE_MAX_CHARS == 300


def test_error_iff_status_error():
    with pytest.raises(ValueError):
        SkillResponse(schema_version=1, skill="walk", status="ok", error=SkillError("x", "y"))
    with pytest.raises(ValueError):
        SkillResponse(schema_version=1, skill="walk", status="error")
