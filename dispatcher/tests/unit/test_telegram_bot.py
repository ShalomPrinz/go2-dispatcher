"""Telegram handlers called directly with fake objects (docs/testing.md)."""

from __future__ import annotations

import asyncio
import os
import signal
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from dispatcher import prompts
from dispatcher.dispatcher import Dispatcher
from dispatcher.models import BusyError, Plan, PlanStep
from dispatcher.registry import Registry
from dispatcher.runlog import RunLogFactory
from dispatcher.transports import telegram_bot
from dispatcher.transports.telegram_bot import (
    SHUTDOWN_EXTRA_S,
    build_application,
    on_post_init,
    on_post_stop,
    on_start,
    on_stop,
    on_text,
)
from dispatcher.tests.helpers import (
    FakeClock,
    FakeExecutor,
    ScriptedPlanner,
    exec_result,
    fake_context,
    fake_update,
    make_config,
    replies,
)
from tests.helpers import REPO_ROOT

USER = 42
TOKEN = "123456:TEST-token-not-real"


@pytest.fixture(scope="module")
def registry():
    return Registry.load(REPO_ROOT / "skills" / "catalog")


@pytest.fixture
def cfg(tmp_path):
    return make_config(tmp_path, telegram={"allowed_user_ids": [USER]})


def mock_dispatcher(*, busy=False, stop="idle"):
    d = MagicMock(spec=Dispatcher)
    d.is_busy.return_value = busy
    d.request_stop.return_value = stop
    return d


def call(handler, update, context):
    asyncio.run(handler(update, context))


def test_unauthorised_user_ignored(cfg):
    d = mock_dispatcher()
    for handler, text in ((on_text, "walk forward"), (on_text, "stop"), (on_stop, "/stop"),
                          (on_start, "/start")):
        u = fake_update(text, user_id=7)
        call(handler, u, fake_context(dispatcher=d, cfg=cfg))
        assert replies(u) == []
    assert d.mock_calls == []


def test_missing_user_or_message_ignored(cfg):
    d = mock_dispatcher()
    u = fake_update("walk", user_id=None)
    call(on_text, u, fake_context(dispatcher=d, cfg=cfg))
    assert replies(u) == []
    call(on_text, SimpleNamespace(effective_user=SimpleNamespace(id=USER),
                                  effective_message=None),
         fake_context(dispatcher=d, cfg=cfg))
    assert d.mock_calls == []


def test_start_replies_help(cfg):
    u = fake_update("/start", USER)
    call(on_start, u, fake_context(dispatcher=mock_dispatcher(), cfg=cfg))
    assert replies(u) == [prompts.help_text(cfg.robot.backend)]


@pytest.mark.parametrize("handler,text", [(on_text, "stop"), (on_text, "  Stop "),
                                          (on_stop, "/stop")])
def test_stop_while_idle(cfg, handler, text):
    d = mock_dispatcher(stop="idle")
    u = fake_update(text, USER)
    call(handler, u, fake_context(dispatcher=d, cfg=cfg))
    d.request_stop.assert_called_once_with("telegram")
    assert replies(u) == [prompts.NOTHING_RUNNING]


@pytest.mark.parametrize("handler,text", [(on_text, "stop"), (on_stop, "/stop")])
def test_stop_while_busy(cfg, handler, text):
    d = mock_dispatcher(busy=True, stop="stopping")
    u = fake_update(text, USER)
    call(handler, u, fake_context(dispatcher=d, cfg=cfg))
    d.request_stop.assert_called_once_with("telegram")
    d.run_task.assert_not_called()
    assert replies(u) == [prompts.STOPPING]


def test_task_while_busy(cfg):
    d = mock_dispatcher(busy=True)
    u = fake_update("walk forward", USER)
    call(on_text, u, fake_context(dispatcher=d, cfg=cfg))
    d.run_task.assert_not_called()
    assert replies(u) == [prompts.BUSY]


def test_busy_race(cfg):
    d = mock_dispatcher()
    d.run_task.side_effect = BusyError()
    u = fake_update("walk forward", USER)
    call(on_text, u, fake_context(dispatcher=d, cfg=cfg))
    assert replies(u) == [prompts.WORKING, prompts.BUSY]


def test_empty_task(cfg):
    d = mock_dispatcher()
    u = fake_update("   ", USER)
    call(on_text, u, fake_context(dispatcher=d, cfg=cfg))
    d.run_task.assert_not_called()
    assert replies(u) == [prompts.EMPTY_TASK]


def test_handler_exception_replies_error(cfg, capsys):
    d = mock_dispatcher()
    d.run_task.side_effect = RuntimeError("boom")
    u = fake_update("walk forward", USER)
    call(on_text, u, fake_context(dispatcher=d, cfg=cfg))
    assert replies(u) == [prompts.WORKING, "Error: RuntimeError"]
    assert "RuntimeError: boom" in capsys.readouterr().err


def test_normal_task(cfg, registry):
    turn = PlanStep(skill="turn", params={"direction": "left", "angle_deg": 90})
    planner = ScriptedPlanner([Plan(status="PLAN", steps=[turn]),
                               Plan(status="DONE", message="Turned left.")])
    d = Dispatcher(cfg, registry, planner, FakeExecutor([exec_result("ok", skill="turn")]),
                   RunLogFactory(cfg.log.dir, session_id="s1"), clock=FakeClock(),
                   initial_posture="standing")
    u = fake_update("  turn left  ", USER)
    call(on_text, u, fake_context(dispatcher=d, cfg=cfg))
    got = replies(u)
    assert got[0] == prompts.WORKING
    assert len(got) == 2
    assert got[1].startswith("DONE: Turned left.\nSteps: 1 run, 0 failed\n1. turn(")
    assert not d.is_busy()


def test_run_task_arguments(cfg, registry, monkeypatch):
    d = mock_dispatcher()
    d.registry = registry
    d.run_task.return_value = MagicMock()
    u = fake_update(" sit down ", USER)
    ctx = fake_context(dispatcher=d, cfg=cfg)
    monkeypatch.setattr(telegram_bot, "format_outcome", lambda outcome, reg: "formatted")
    call(on_text, u, ctx)
    d.run_task.assert_called_once_with("sit down", source="telegram", sender_id=str(USER))
    assert replies(u) == [prompts.WORKING, "formatted"]


def test_build_application(cfg):
    d = mock_dispatcher()
    app = build_application(d, cfg, TOKEN)
    assert app.update_processor.max_concurrent_updates > 1
    assert app.post_stop is on_post_stop
    assert app.post_init is on_post_init
    assert app.bot_data["dispatcher"] is d and app.bot_data["cfg"] is cfg
    asyncio.run(app.post_stop(app))
    d.shutdown.assert_called_once_with(cfg.robot.stop_move_timeout_s + SHUTDOWN_EXTRA_S)


class _BlockingDispatcher:
    """Dispatcher stand-in whose ``run_task`` blocks until ``request_stop`` (or a safety
    timeout); records the order of calls."""

    def __init__(self, registry, release_after_s):
        self.registry = registry
        self.events: list[str] = []
        self.started = threading.Event()
        self._stopped = threading.Event()
        self._release_after_s = release_after_s
        self._busy = False

    def is_busy(self):
        return self._busy

    def run_task(self, text, *, source, sender_id=None):
        self._busy = True
        self.started.set()
        killed = self._stopped.wait(self._release_after_s)
        self.events.append("task_stopped" if killed else "task_ran_to_time_limit")
        self._busy = False
        return "outcome"

    def request_stop(self, source):
        self.events.append(f"request_stop:{source}")
        if self._busy:
            self._stopped.set()
            return "stopping"
        return "idle"

    def shutdown(self, wait_s):
        self.events.append("shutdown")


def test_stop_signal_kills_task_before_ptb_waits_for_handlers(cfg, registry, monkeypatch):
    """SIGTERM during a running task: the task is stopped promptly, before PTB's
    ``Application.stop()`` waits for in-flight handlers and before ``post_stop``. Runs the
    real ``run_polling`` with network calls patched out."""
    from telegram import Update, User
    from telegram.ext import ExtBot, Updater

    async def noop(*args, **kwargs):
        return None

    async def bot_initialize(self):            # instead of getMe over the network
        self._bot_user = User(id=1, is_bot=True, first_name="bot", username="test_bot")

    sent: list[str] = []

    async def send_message(self, chat_id, text, *args, **kwargs):
        sent.append(text)

    monkeypatch.setattr(ExtBot, "initialize", bot_initialize)
    monkeypatch.setattr(ExtBot, "shutdown", noop)
    monkeypatch.setattr(ExtBot, "send_message", send_message)
    monkeypatch.setattr(Updater, "start_polling", noop)
    monkeypatch.setattr(telegram_bot, "format_outcome", lambda outcome, reg: "formatted")

    release_after_s = 5.0              # old behaviour: the task only ends at this timeout
    d = _BlockingDispatcher(registry, release_after_s)
    app = build_application(d, cfg, TOKEN)
    update = Update.de_json({
        "update_id": 1,
        "message": {"message_id": 1, "date": 0, "text": "walk forward",
                    "chat": {"id": USER, "type": "private"},
                    "from": {"id": USER, "is_bot": False, "first_name": "Op"}},
    }, app.bot)
    app.update_queue.put_nowait(update)

    def send_signal():
        if d.started.wait(5):
            os.kill(os.getpid(), signal.SIGTERM)

    t0 = time.monotonic()
    threading.Thread(target=send_signal, daemon=True).start()
    app.run_polling(close_loop=False)
    elapsed = time.monotonic() - t0

    assert d.events[:2] == ["request_stop:shutdown", "task_stopped"]
    assert d.events[-1] == "shutdown"          # post_stop backstop still runs
    assert elapsed < release_after_s
    assert sent == [prompts.WORKING, "formatted"]
