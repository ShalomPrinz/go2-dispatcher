"""Test skill: turn under another name; commands motion."""

from __future__ import annotations

from dataclasses import replace

from skills import turn
from skills.runtime import runner

POLICY = replace(turn.POLICY, name="spin")
body = turn.body


def main() -> None:
    runner.run_skill(POLICY, body)
