"""Transport start-up shared by the CLI and Telegram: planner choice and initial
posture (docs/running.md)."""

from __future__ import annotations

import pytest

from dispatcher.tests.helpers import FakeExecutor, make_config
from dispatcher.transports import (
    API_KEY_ENV,
    MISSING_API_KEY,
    TEST_PLANNER_ENV,
    initial_posture,
    make_planner,
)


@pytest.fixture
def no_planner_env(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    monkeypatch.delenv(TEST_PLANNER_ENV, raising=False)
    return monkeypatch


def test_missing_api_key_exits_2(tmp_path, no_planner_env, capsys):
    with pytest.raises(SystemExit) as ei:
        make_planner(make_config(tmp_path))
    assert ei.value.code == 2
    assert capsys.readouterr().err.strip() == MISSING_API_KEY


def test_malformed_test_planner_exits_2(tmp_path, no_planner_env):
    no_planner_env.setenv(TEST_PLANNER_ENV, "planner_factory")
    with pytest.raises(SystemExit) as ei:
        make_planner(make_config(tmp_path))
    assert ei.value.code == 2


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
