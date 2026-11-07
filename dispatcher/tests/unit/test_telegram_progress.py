"""Telegram live progress: coalesced edits, best-effort sending, nothing in the task's timing (docs/running.md)."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from dispatcher import prompts
from dispatcher.context import PromptSurface
from dispatcher.dispatcher import Dispatcher
from dispatcher.models import Plan, PlanStep
from dispatcher.registry import Registry
from dispatcher.tests.helpers import FakeExecutor, ScriptedPlanner, exec_result, fake_context, make_config, replies
from dispatcher.transports.telegram_bot import on_text
from dispatcher.transports.telegram_progress import TelegramProgress
from tests.helpers import REPO_ROOT

USER = 42
EDIT_S = 0.5


@pytest.fixture(scope="module")
def registry():
    return Registry.load(REPO_ROOT / "skills" / "catalog")


@pytest.fixture
def cfg(tmp_path):
    return make_config(tmp_path, telegram={"allowed_user_ids": [USER]})


def call(handler, update, context):
    asyncio.run(handler(update, context))


def make_dispatcher(cfg, registry, progress):
    turn = PlanStep(skill="turn", params={"direction": "left", "angle_deg": 90})
    surface = PromptSurface.build(registry, cfg.loop.planning_horizon)
    planner = ScriptedPlanner([Plan(status="PLAN", steps=[turn]), Plan(status="DONE", message="Turned left.")], surface)
    executor = FakeExecutor([exec_result("ok", skill="turn")])
    return Dispatcher(cfg, registry, planner, executor, initial_posture="standing", observer=progress)


def test_edits_are_coalesced(registry):
    edits: list[str] = []

    async def edit(text):
        edits.append(text)

    async def main():
        progress = TelegramProgress(registry, interval_s=0.2)
        assert progress.open(edit)
        burst = threading.Thread(target=lambda: [progress.stop_requested(f"s{i}", "between") for i in range(30)])
        burst.start()
        burst.join()
        await progress.close()
        return progress.text()

    final = asyncio.run(main())
    assert 1 <= len(edits) <= 2
    assert edits[-1] == final and final.endswith(prompts.PROGRESS_STOP_REQUESTED.format(source="s29"))


def test_slow_edits_stay_out_of_task_timing(cfg, registry):
    edits: list[str] = []

    async def slow_edit(text):
        await asyncio.sleep(EDIT_S)
        edits.append(text)

    async def main():
        progress = TelegramProgress(registry, interval_s=0.0)
        d = make_dispatcher(cfg, registry, progress)
        assert progress.open(slow_edit)
        outcome = await asyncio.to_thread(d.run_task, "turn left", source="telegram")
        await progress.close()
        return outcome, progress.text()

    outcome, final = asyncio.run(main())
    assert outcome.outcome == "DONE"
    assert outcome.duration_ms < EDIT_S * 1000  # the task never waited for an edit
    assert edits[-1] == final
    lines = final.splitlines()
    assert lines[0] == prompts.PROGRESS_PLAN
    assert lines[2].startswith(prompts.PROGRESS_STEP_OK.format(step="turn("))
    assert lines[-1] == prompts.PROGRESS_PLAN_END.format(status="DONE", message="Turned left.")
    assert prompts.PROGRESS_THINKING not in final and "tokens" not in final and "Thought" not in final


def test_failed_edit_is_logged_and_task_still_replies(cfg, registry, capsys):
    progress = TelegramProgress(registry, interval_s=0.0)
    d = make_dispatcher(cfg, registry, progress)
    working = SimpleNamespace(edit_text=AsyncMock(side_effect=RuntimeError("network down")))
    u = SimpleNamespace(
        effective_user=SimpleNamespace(id=USER),
        effective_message=SimpleNamespace(text="turn left", reply_text=AsyncMock(return_value=working)),
    )
    call(on_text, u, fake_context(dispatcher=d, cfg=cfg, progress=progress))
    got = replies(u)
    assert got[0] == prompts.WORKING and got[1].startswith("DONE: Turned left.")
    assert working.edit_text.await_count >= 1
    assert "Telegram progress update failed: RuntimeError" in capsys.readouterr().err
