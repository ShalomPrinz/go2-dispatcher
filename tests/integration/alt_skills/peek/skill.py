"""Test skill: detect_object under another name; a vision service client."""

from __future__ import annotations

from dataclasses import replace

from services.vision import client  # noqa: F401  the vision dependency the registry checks against uses_vision
from skills import detect_object
from skills.runtime import runner

POLICY = replace(detect_object.POLICY, name="peek")
body = detect_object.body


def main() -> None:
    runner.run_skill(POLICY, body)
