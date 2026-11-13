"""The skill-side SkillResponse: one-line error messages (contract/docs/contract.md). The error-iff-status rule
is checked in contract/tests/unit/test_schema.py, validation by the dispatcher in
tests/integration/test_skill_response.py."""

from __future__ import annotations

from contract.schema import ErrorCode
from skills.runner import SkillOutcome, StateSampler, build_response


def test_message_collapsed_not_cut():
    msg = "line one\nline   two\r\n\tthree " + "x" * 400
    outcome = SkillOutcome.error(ErrorCode.EXCEPTION, msg, observations={}, timing={})
    error = build_response("walk", outcome, StateSampler(), 1.0).error
    assert error is not None
    out = error.message
    assert "\n" not in out and "\r" not in out and "\t" not in out
    assert out.startswith("line one line two three x")
    assert out.endswith("x" * 400)
