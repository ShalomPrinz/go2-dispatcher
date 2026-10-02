"""build_response cuts error messages to one line (skills/docs/skills.md).
That its output validates against the dispatcher's SkillResponse is checked in
tests/integration/test_skill_response.py."""

from __future__ import annotations

from skills import result
from skills.result import build_response


def test_message_collapsed_and_cut():
    msg = "line one\nline   two\r\n\tthree " + "x" * 400
    d = build_response("walk", "error", error_code="exception", error_message=msg)
    out = d["error"]["message"]
    assert "\n" not in out and "\r" not in out and "\t" not in out
    assert out.startswith("line one line two three x")
    assert len(out) == result.ERROR_MESSAGE_MAX_CHARS == 300
