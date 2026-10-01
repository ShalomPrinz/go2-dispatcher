"""build_response rejects inconsistent arguments and cuts error messages (skills/docs/skills.md).
That its output validates against the dispatcher's SkillResponse is checked in
tests/integration/test_skill_response.py."""

from __future__ import annotations

import pytest

from skills import result
from skills.result import build_response


@pytest.mark.parametrize("kwargs", [
    {"error_message": "boom"},
    {"error_code": "sdk_error"},
], ids=["missing_code", "missing_message"])
def test_error_without_code_or_message_raises(kwargs):
    with pytest.raises(ValueError):
        build_response("walk", "error", **kwargs)


def test_ok_with_error_code_raises():
    with pytest.raises(ValueError):
        build_response("walk", "ok", error_code="x", error_message="y")


def test_bad_status_raises():
    with pytest.raises(ValueError):
        build_response("walk", "timeout")


def test_message_collapsed_and_cut():
    msg = "line one\nline   two\r\n\tthree " + "x" * 400
    d = build_response("walk", "error", error_code="exception", error_message=msg)
    out = d["error"]["message"]
    assert "\n" not in out and "\r" not in out and "\t" not in out
    assert out.startswith("line one line two three x")
    assert len(out) == result.ERROR_MESSAGE_MAX_CHARS == 300
