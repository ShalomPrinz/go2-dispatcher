"""The executor parses the response line from skill stdout (skills/docs/skills.md)."""

from __future__ import annotations

import pytest

from dispatcher.executor import _parse_response
from skills.result import SkillOutcome, StateSampler, build_response, to_json
from skills.schema import ErrorCode

LINE = to_json(build_response("walk", SkillOutcome.ok(observations={}, timing={}), StateSampler(), 0.0))


@pytest.mark.parametrize(
    "stdout",
    [
        "junk\n"
        + to_json(
            build_response(
                "walk",
                SkillOutcome.error(ErrorCode.SDK_ERROR, "y", observations={}, timing={}),
                StateSampler(),
                0.0,
            )
        )
        + "\n"
        + LINE,
        LINE + "\n\n  \n",
    ],
    ids=["last_line_wins", "trailing_blank_lines"],
)
def test_parse_response_last_nonempty_line(stdout):
    r = _parse_response(stdout, "walk")
    assert r is not None and r.status == "ok"


@pytest.mark.parametrize(
    "stdout", ["", LINE + "\n{not json", LINE.replace("walk", "turn")], ids=["empty", "invalid_json", "other_skill"]
)
def test_parse_response_none(stdout):
    assert _parse_response(stdout, "walk") is None
