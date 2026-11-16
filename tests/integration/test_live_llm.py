"""Live stub run against the real Anthropic API (``--run-live``, tests/docs/testing.md)."""

from __future__ import annotations

import json
import os

import pytest

from dispatcher.app import build_executor
from dispatcher.context import build_prompt_surface
from dispatcher.dispatcher import Dispatcher
from dispatcher.llm.anthropic_planner import AnthropicPlanner
from dispatcher.registry import Registry
from dispatcher.tests.helpers import BUILTIN_SKILLS_DIR, make_config, make_runlog
from services.backend import stub_state

pytestmark = [pytest.mark.live_llm, pytest.mark.timeout(300)]

TASK = "turn left 90 degrees, then tell me if you see a chair"


def test_live_turn_and_find_chair(tmp_path):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        pytest.skip("ANTHROPIC_API_KEY is not set")
    cfg = make_config(tmp_path, stub={"objects": {"chair": {"x": 0.0, "y": 2.0}}})
    stub_state.reset(cfg.stub.initial_posture, cfg.stub.state_file)
    registry = Registry.load(BUILTIN_SKILLS_DIR)
    surface = build_prompt_surface(registry, cfg.loop.planning_horizon)
    planner = AnthropicPlanner(key, cfg.llm, surface)
    d = Dispatcher(cfg, registry, planner, build_executor(cfg), runlog=make_runlog(cfg, planner))
    try:
        o = d.run_task(TASK, source="test")
    finally:
        d.shutdown(0)  # stops the vision service
    print(f"run log: {o.log_path}")
    assert o.outcome == "DONE", o.message
    assert "chair" in o.message.lower()
    with open(o.log_path, encoding="utf-8") as f:
        types = [json.loads(line)["type"] for line in f]
    assert "plan_invalid" not in types
