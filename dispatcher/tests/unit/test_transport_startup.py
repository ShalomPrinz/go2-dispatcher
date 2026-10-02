"""Transport start-up shared by the CLI and Telegram: planner choice and initial
posture (docs/running.md)."""

from __future__ import annotations

import pytest

from dispatcher.dispatcher import Dispatcher
from dispatcher.registry import Registry
from dispatcher.runlog import RunLogFactory
from dispatcher.tests.helpers import FakeExecutor, make_config
from dispatcher.transports import (
    API_KEY_ENV,
    MISSING_API_KEY,
    NO_PLANNER_DETAIL,
    TEST_PLANNER_ENV,
    initial_posture,
    make_planner,
)
from tests.helpers import REPO_ROOT


@pytest.fixture
def no_planner_env(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    monkeypatch.delenv(TEST_PLANNER_ENV, raising=False)
    return monkeypatch


def test_missing_api_key_exits_2(tmp_path, no_planner_env, capsys):
    with pytest.raises(SystemExit) as ei:
        make_planner(make_config(tmp_path), need_llm=True)
    assert ei.value.code == 2
    assert capsys.readouterr().err.strip() == MISSING_API_KEY


def test_malformed_test_planner_exits_2(tmp_path, no_planner_env):
    no_planner_env.setenv(TEST_PLANNER_ENV, "planner_factory")
    with pytest.raises(SystemExit) as ei:
        make_planner(make_config(tmp_path), need_llm=True)
    assert ei.value.code == 2


def test_no_planner_ends_task_llm_error(tmp_path, no_planner_env):
    cfg = make_config(tmp_path)
    d = Dispatcher(
        cfg,
        Registry.load(REPO_ROOT / "skills" / "catalog"),
        make_planner(cfg, need_llm=False),
        FakeExecutor(),
        RunLogFactory(cfg.log.dir, session_id="s1"),
        initial_posture="standing",
    )
    o = d.run_task("sit down", source="test")
    assert o.outcome == "LLM_ERROR"
    assert NO_PLANNER_DETAIL in o.message


def test_real_backend_without_state_is_unknown(tmp_path, capsys):
    cfg = make_config(tmp_path, robot={"backend": "real", "network_interface": "eth0"})
    assert initial_posture(cfg, FakeExecutor(), reset=False) == "unknown"
    assert "Warning" in capsys.readouterr().err


def test_unreadable_stub_state_is_unknown(tmp_path):
    cfg = make_config(tmp_path)
    cfg.stub.state_file.parent.mkdir(parents=True, exist_ok=True)
    cfg.stub.state_file.write_text("{not json", encoding="utf-8")
    assert initial_posture(cfg, FakeExecutor(), reset=False) == "unknown"
    assert initial_posture(cfg, FakeExecutor(), reset=True) == cfg.stub.initial_posture
