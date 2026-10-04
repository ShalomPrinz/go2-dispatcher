"""Transport start-up shared by the CLI and Telegram: planner choice and initial
posture (docs/running.md)."""

from __future__ import annotations

import pytest

from dispatcher.context import PromptSurface
from dispatcher.models import RobotState
from dispatcher.registry import Registry
from dispatcher.tests.helpers import FakeExecutor, make_config
from dispatcher.tests.helpers.fakes import state
from dispatcher.transports import (
    API_KEY_ENV,
    MISSING_API_KEY,
    TEST_PLANNER_ENV,
    initial_posture,
    make_planner,
)
from tests.helpers import REPO_ROOT


@pytest.fixture(scope="module")
def surface():
    return PromptSurface.build(Registry.load(REPO_ROOT / "skills" / "catalog"), 5)


@pytest.fixture
def no_planner_env(monkeypatch):
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    monkeypatch.delenv(TEST_PLANNER_ENV, raising=False)
    return monkeypatch


def test_missing_api_key_exits_2(tmp_path, surface, no_planner_env, capsys):
    with pytest.raises(SystemExit) as ei:
        make_planner(make_config(tmp_path), surface)
    assert ei.value.code == 2
    assert capsys.readouterr().err.strip() == MISSING_API_KEY


def test_malformed_test_planner_exits_2(tmp_path, surface, no_planner_env):
    no_planner_env.setenv(TEST_PLANNER_ENV, "planner_factory")
    with pytest.raises(SystemExit) as ei:
        make_planner(make_config(tmp_path), surface)
    assert ei.value.code == 2


def test_failed_state_read_is_unknown_with_warning(capsys):
    assert initial_posture(FakeExecutor()) == "unknown"
    assert "Warning" in capsys.readouterr().err


def test_posture_comes_from_state_read():
    class SittingExecutor(FakeExecutor):
        def read_state(self) -> RobotState | None:
            return state("sitting")

    assert initial_posture(SittingExecutor()) == "sitting"
