"""Live stub run against the real Anthropic API (``--run-live``, docs/testing.md)."""

from __future__ import annotations

import json
import os

import pytest

from dispatcher.dispatcher import Dispatcher
from dispatcher.executor import Executor
from dispatcher.llm import AnthropicPlanner
from dispatcher.registry import Registry
from dispatcher.runlog import RunLogFactory
from skills import stub
from dispatcher.tests.helpers import make_config
from tests.helpers import REPO_ROOT

pytestmark = [pytest.mark.live_llm, pytest.mark.timeout(300)]

TASK = "turn left 90 degrees, then tell me if you see a chair"


def test_live_turn_and_find_chair(tmp_path):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        pytest.skip("ANTHROPIC_API_KEY is not set")
    cfg = make_config(tmp_path, stub={"detections": {"chair": "center:near"}})
    stub.write_posture(cfg.stub.initial_posture, cfg.stub.state_file)
    d = Dispatcher(cfg, Registry.load(REPO_ROOT / "skills" / "catalog"),
                   AnthropicPlanner(key, cfg.llm, cfg.loop.planning_horizon),
                   Executor(cfg, cfg.base_dir), RunLogFactory(cfg.log.dir, "live"),
                   initial_posture=cfg.stub.initial_posture)
    o = d.run_task(TASK, source="test")
    print(f"run log: {o.log_path}")
    assert o.outcome == "DONE", o.message
    assert "chair" in o.message.lower()
    with open(o.log_path, encoding="utf-8") as f:
        types = [json.loads(line)["type"] for line in f]
    assert "plan_invalid" not in types and "horizon_rejection" not in types
