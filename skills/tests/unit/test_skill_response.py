"""The skill-side SkillResponse: one-line error messages and the error-iff-status rule
(skills/docs/skills.md). Validation by the dispatcher is checked in
tests/integration/test_skill_response.py."""

from __future__ import annotations

import pytest

from skills.runner import SkillOutcome, StateSampler, build_response
from skills.schema import ErrorCode, SkillError, SkillResponse


def test_message_collapsed_not_cut():
    msg = "line one\nline   two\r\n\tthree " + "x" * 400
    outcome = SkillOutcome.error(ErrorCode.EXCEPTION, msg, observations={}, timing={})
    error = build_response("walk", outcome, StateSampler(), 1.0).error
    assert error is not None
    out = error.message
    assert "\n" not in out and "\r" not in out and "\t" not in out
    assert out.startswith("line one line two three x")
    assert out.endswith("x" * 400)


def test_error_iff_status_error():
    with pytest.raises(ValueError):
        SkillResponse(schema_version=1, skill="walk", status="ok", error=SkillError("x", "y"))
    with pytest.raises(ValueError):
        SkillResponse(schema_version=1, skill="walk", status="error")
