"""Telegram live progress: one message per model call, edits only on change, best-effort sending, nothing in the
task's timing (docs/running.md)."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from dispatcher import prompts
from dispatcher.context import PromptSurface
from dispatcher.dispatcher import Dispatcher
from dispatcher.models import ObstacleDistances, Plan, PlanStep, RobotState
from dispatcher.registry import Registry
from dispatcher.tests.helpers import FakeExecutor, ScriptedPlanner, exec_result, fake_context, make_config, replies
from dispatcher.transports.telegram_bot import on_text
from dispatcher.transports.telegram_progress import TelegramProgress, robot_line
from tests.helpers import REPO_ROOT

USER = 42
EDIT_S = 0.5
TURN = PlanStep(skill="turn", params={"direction": "left", "angle_deg": 90})
WALK = PlanStep(skill="walk", params={"direction": "forward", "distance_m": 1.0})


@pytest.fixture(scope="module")
def registry():
    return Registry.load(REPO_ROOT / "skills" / "catalog")


@pytest.fixture
def cfg(tmp_path):
    return make_config(tmp_path, telegram={"allowed_user_ids": [USER]})


def call(handler, update, context):
    asyncio.run(handler(update, context))


class Phone:
    """Records progress messages: ``messages[i]`` is the text history of the i-th message."""

    def __init__(self, delay_s: float = 0.0):
        self.messages: list[list[str]] = [[prompts.WORKING]]
        self.delay_s = delay_s

    def editor(self, i: int):
        async def edit(text):
            await asyncio.sleep(self.delay_s)
            self.messages[i].append(text)

        return edit

    async def new_message(self, text):
        self.messages.append([text])
        return self.editor(len(self.messages) - 1)

    def final(self) -> list[str]:
        return [m[-1] for m in self.messages]


def make_dispatcher(cfg, registry, progress, items, results):
    surface = PromptSurface.build(registry, cfg.loop.planning_horizon)
    return Dispatcher(cfg, registry, ScriptedPlanner(items, surface), FakeExecutor(results), observer=progress)


async def run(d, progress, phone, task="turn left"):
    assert progress.open(phone.editor(0), phone.new_message)
    outcome = await asyncio.to_thread(d.run_task, task, source="telegram")
    await progress.close()
    return outcome


def test_edits_are_coalesced(registry):
    phone = Phone()

    async def main():
        progress = TelegramProgress(registry, interval_s=0.2)
        assert progress.open(phone.editor(0), phone.new_message)
        burst = threading.Thread(target=lambda: [progress.stop_requested(f"s{i}", "between") for i in range(30)])
        burst.start()
        burst.join()
        await progress.close()
        return progress.text()

    final = asyncio.run(main())
    assert 2 <= len(phone.messages[0]) <= 3  # the sent text, then one or two edits
    assert phone.final() == [final] and final.endswith(prompts.PROGRESS_STOP_REQUESTED.format(source="s29"))


def test_slow_edits_stay_out_of_task_timing(cfg, registry):
    phone = Phone(delay_s=EDIT_S)
    progress = TelegramProgress(registry, interval_s=0.0)
    items = [Plan(status="PLAN", steps=[TURN]), Plan(status="DONE", message="Turned left.")]
    d = make_dispatcher(cfg, registry, progress, items, [exec_result("ok", skill="turn")])
    outcome = asyncio.run(run(d, progress, phone))
    assert outcome.outcome == "DONE"
    assert outcome.duration_ms < EDIT_S * 1000  # the task never waited for an edit
    assert phone.final() == progress.texts()


def test_each_model_call_gets_its_own_message(cfg, registry):
    """A malformed reply, its retry, a failed step, a bounds-rejected plan, a replan and DONE: one message per
    model call, blank lines around the step list, and no validation or skill error detail."""
    phone = Phone()
    progress = TelegramProgress(registry, interval_s=0.0)
    items = [
        {"status": "PLAN", "steps": []},
        Plan(status="PLAN", message="Turn <then> walk.", steps=[TURN, WALK]),
        Plan(status="PLAN", steps=[PlanStep(skill="walk", params={"distance_m": 1.0})]),
        Plan(status="PLAN", steps=[TURN]),
        Plan(status="DONE", message="Turned left twice; the walk was refused."),
    ]
    results = [
        exec_result("ok", skill="turn"),
        exec_result("error", skill="walk", error_code="motion_rejected", error_message="Move returned 3104"),
        exec_result("ok", skill="turn"),
    ]
    d = make_dispatcher(cfg, registry, progress, items, results)
    outcome = asyncio.run(run(d, progress, phone))
    assert outcome.outcome == "DONE"
    final = phone.final()
    assert final == progress.texts() and len(final) == 5
    malformed = prompts.PROGRESS_PLAN_MALFORMED.format(then=prompts.PROGRESS_ASKING_AGAIN)
    assert final[0] == malformed
    second = final[1].splitlines()
    assert second[:5] == [
        prompts.PROGRESS_PLAN_MESSAGE.format(message="Turn &lt;then&gt; walk."),
        "",
        "1. turn(direction=left, angle_deg=90)",
        "2. walk(direction=forward, distance_m=1)",
        "",
    ]
    assert second[5].startswith("✅ turn(direction=left, angle_deg=90) -&gt; ok (")
    assert second[6].startswith("❌ walk(direction=forward, distance_m=1) failed: the robot refused the move (")
    assert len(second) == 7
    assert final[2].splitlines() == [prompts.PROGRESS_PLAN, "", "1. walk(distance_m=1)", "", malformed]
    assert final[3].splitlines()[:3] == [prompts.PROGRESS_PLAN, "", "1. turn(direction=left, angle_deg=90)"]
    assert final[4] == prompts.PROGRESS_PLAN_DONE.format(message="Turned left twice; the walk was refused.")
    text = "\n".join(final)
    assert "3104" not in text and "missing" not in text
    assert prompts.PROGRESS_THINKING not in text  # every round's message ends with its own lines


def test_plan_without_results_has_no_trailing_blank_line(registry):
    phone = Phone()

    async def main():
        progress = TelegramProgress(registry, interval_s=0.0)
        assert progress.open(phone.editor(0), phone.new_message)
        progress.plan(1, Plan(status="PLAN", steps=[TURN]), 1)
        await progress.close()

    asyncio.run(main())
    assert phone.final() == [prompts.PROGRESS_PLAN + "\n\n1. turn(direction=left, angle_deg=90)"]


def test_rejection_that_ends_the_task_gives_up(registry):
    phone = Phone()

    async def main():
        progress = TelegramProgress(registry, interval_s=0.0)
        assert progress.open(phone.editor(0), phone.new_message)
        progress.llm_request(1, "initial", None, "")
        progress.plan_invalid(1, None, None, 5)  # type: ignore[arg-type]
        progress.llm_request(2, "schema_retry", "initial", "")
        progress.plan_invalid(2, None, None, 5)  # type: ignore[arg-type]
        progress.task_end(None, rejections=0, horizon_rejections=0, usage_totals={}, budget=None)  # type: ignore[arg-type]
        await progress.close()

    asyncio.run(main())
    assert phone.final() == [  # the schema retry gets its own message
        prompts.PROGRESS_PLAN_MALFORMED.format(then=prompts.PROGRESS_ASKING_AGAIN),
        prompts.PROGRESS_PLAN_MALFORMED.format(then=prompts.PROGRESS_GIVING_UP),
    ]


def test_no_edit_when_the_text_is_unchanged(registry):
    phone = Phone()
    standing = RobotState(t=0.0, backend="stub", posture="standing")

    async def main():
        progress = TelegramProgress(registry, interval_s=0.0)
        assert progress.open(phone.editor(0), phone.new_message)
        progress.robot_sample(standing, "up", 1.0)
        await asyncio.sleep(0.05)
        for i in range(20):  # same rendered line: no edits
            progress.robot_sample(standing, "up", 2.0 + i)
            await asyncio.sleep(0.005)
        progress.robot_link("down", None)
        await progress.close()

    asyncio.run(main())
    up = prompts.WORKING + "\n" + robot_line("up", standing)
    assert phone.messages == [
        [prompts.WORKING, up, up.replace(robot_line("up", standing), robot_line("down", standing))]
    ]


def test_failed_edit_is_logged_and_task_still_replies(cfg, registry, capsys):
    progress = TelegramProgress(registry, interval_s=0.0)
    items = [Plan(status="PLAN", steps=[TURN]), Plan(status="DONE", message="Turned left.")]
    d = make_dispatcher(cfg, registry, progress, items, [exec_result("ok", skill="turn")])
    failing = SimpleNamespace(edit_text=AsyncMock(side_effect=RuntimeError("network down")))
    u = SimpleNamespace(
        effective_user=SimpleNamespace(id=USER),
        effective_message=SimpleNamespace(text="turn left", reply_text=AsyncMock(return_value=failing)),
    )
    call(on_text, u, fake_context(dispatcher=d, cfg=cfg, progress=progress))
    got = replies(u)
    assert got[0] == prompts.WORKING and got[-1].startswith("✅ <b>Done</b>: Turned left.")
    assert failing.edit_text.await_count >= 1
    assert "Telegram progress update failed: RuntimeError" in capsys.readouterr().err


def test_robot_line():
    walking = RobotState(
        t=0.0,
        backend="real",
        posture="standing",
        velocity=[0.3, 0.4, 0.0],
        yaw_speed=0.2,
        obstacle_m=ObstacleDistances(front=1.234, left=0.8),
    )
    assert robot_line("up", walking) == (
        "🟢 Robot: link up | standing | 0.50 m/s, turn 11 deg/s | obstacles m F 1.2 B - L 0.8 R -"
    )
    assert robot_line("down", walking).startswith("🔴 Robot: link down | standing |")
    bare = RobotState(t=0.0, backend="stub", posture="sitting")
    assert robot_line("up", bare) == "🟢 Robot: link up | sitting | speed n/a | obstacles n/a"
    assert robot_line("up", None) == "🟢 Robot: link up | no state yet"


def test_robot_line_follows_the_stream_and_leaves_at_task_end(registry):
    async def edit(text):
        pass

    async def new_message(text):
        return edit

    async def main():
        progress = TelegramProgress(registry)
        progress.robot_link("up", None)  # no session yet: dropped
        assert progress.open(edit, new_message)
        progress.robot_link("up", None)
        progress.robot_sample(RobotState(t=0.0, backend="stub", posture="standing"), "up", 10.0)
        progress.robot_link("down", 10.0)
        live = progress.text()
        progress.task_end(None, rejections=0, horizon_rejections=0, usage_totals={}, budget=None)  # type: ignore[arg-type]
        progress.robot_sample(RobotState(t=0.0, backend="stub", posture="standing"), "up", 10.4)  # after the end
        await progress.close()
        return live, progress.text()

    live, final = asyncio.run(main())
    assert live == prompts.WORKING + "\n🔴 Robot: link down | standing | speed n/a | obstacles n/a"
    assert final == prompts.WORKING
