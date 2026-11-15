"""Test skill: turn under another name; commands motion."""

from __future__ import annotations

from dataclasses import replace

from skills import runner, turn

POLICY = replace(turn.POLICY, name="spin")

if __name__ == "__main__":
    runner.run_skill(POLICY, turn.body)
