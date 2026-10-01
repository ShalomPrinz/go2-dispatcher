"""``go2-bot`` Telegram transport (§16.3): long polling, plain-text replies."""

from __future__ import annotations

import argparse
import asyncio
import functools
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Awaitable, Callable

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .. import prompts
from ..config import Config
from ..dispatcher import Dispatcher
from ..models import BusyError
from . import build_dispatcher, format_outcome, load_config_and_env

__all__ = ["build_application", "on_start", "on_stop", "on_text", "on_post_stop", "main",
           "TOKEN_ENV", "SOURCE", "STOP_WORD", "SHUTDOWN_EXTRA_S"]

SOURCE = "telegram"
TOKEN_ENV = "TELEGRAM_BOT_TOKEN"
STOP_WORD = "stop"                   # matched after strip().lower() (OD-12)
SHUTDOWN_EXTRA_S = 5.0               # post_stop waits stop_move_timeout_s + this (§16.3)
MISSING_TOKEN = f"Missing {TOKEN_ENV}."
NO_ALLOWED_USERS_WARNING = ("Warning: telegram.allowed_user_ids is empty; "
                            "the bot will answer nobody.")
UNAUTHORISED_WARNING = "Warning: ignoring Telegram message from unauthorised user {user_id}."
ERROR_REPLY = "Error: {exception_type}"

Handler = Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]


def _dispatcher(context: Any) -> Dispatcher:
    return context.application.bot_data["dispatcher"]


def _cfg(context: Any) -> Config:
    return context.application.bot_data["cfg"]


def _guarded(fn: Callable[[Any, Any, Any], Awaitable[None]]) -> Handler:
    """Missing user/message → ignore; unauthorised → no reply, stderr warning;
    any exception → traceback on stderr and ``Error: {ExceptionType}`` reply."""

    @functools.wraps(fn)
    async def handler(update: Any, context: Any) -> None:
        user = update.effective_user
        message = update.effective_message
        if user is None or message is None:
            return
        if user.id not in _cfg(context).telegram.allowed_user_ids:
            print(UNAUTHORISED_WARNING.format(user_id=user.id), file=sys.stderr, flush=True)
            return
        try:
            await fn(user, message, context)
        except Exception as e:  # noqa: BLE001 - reported to the operator
            traceback.print_exc(file=sys.stderr)
            await message.reply_text(ERROR_REPLY.format(exception_type=type(e).__name__))

    return handler


async def _stop(message: Any, context: Any) -> None:
    result = _dispatcher(context).request_stop(SOURCE)
    await message.reply_text(prompts.STOPPING if result == "stopping"
                             else prompts.NOTHING_RUNNING)


@_guarded
async def on_start(user: Any, message: Any, context: Any) -> None:
    await message.reply_text(prompts.help_text(_cfg(context).robot.backend))


@_guarded
async def on_stop(user: Any, message: Any, context: Any) -> None:
    await _stop(message, context)


@_guarded
async def on_text(user: Any, message: Any, context: Any) -> None:
    text = (message.text or "").strip()
    if text.lower() == STOP_WORD:
        await _stop(message, context)
        return
    if not text:
        await message.reply_text(prompts.EMPTY_TASK)
        return
    dispatcher = _dispatcher(context)
    if dispatcher.is_busy():
        await message.reply_text(prompts.BUSY)
        return
    await message.reply_text(prompts.WORKING)
    try:
        outcome = await asyncio.to_thread(dispatcher.run_task, text, source=SOURCE,
                                          sender_id=str(user.id))
    except BusyError:
        await message.reply_text(prompts.BUSY)
        return
    await message.reply_text(format_outcome(outcome, dispatcher.registry))


async def on_post_stop(app: Application) -> None:
    dispatcher: Dispatcher = app.bot_data["dispatcher"]
    cfg: Config = app.bot_data["cfg"]
    await asyncio.to_thread(dispatcher.shutdown,
                            cfg.robot.stop_move_timeout_s + SHUTDOWN_EXTRA_S)


def build_application(dispatcher: Dispatcher, cfg: Config, token: str) -> Application:
    app = (ApplicationBuilder().token(token)
           .concurrent_updates(True)        # REQUIRED: otherwise "stop" cannot arrive during a task
           .post_stop(on_post_stop)
           .build())
    msg = filters.UpdateType.MESSAGE        # new messages only; ignore edited messages
    app.add_handler(CommandHandler("start", on_start, filters=msg))
    app.add_handler(CommandHandler("stop", on_stop, filters=msg))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & msg, on_text))
    app.bot_data["dispatcher"] = dispatcher
    app.bot_data["cfg"] = cfg
    return app


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="go2-bot", description="Go2 LLM dispatcher Telegram bot.")
    p.add_argument("--config", type=Path, default=None, help="config file (default ./config.toml)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    cfg = load_config_and_env(args.config, {})
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        print(MISSING_TOKEN, file=sys.stderr)
        return 2
    if not cfg.telegram.allowed_user_ids:
        print(NO_ALLOWED_USERS_WARNING, file=sys.stderr)
    dispatcher = build_dispatcher(cfg, need_llm=True, reset_stub=True)
    build_application(dispatcher, cfg, token).run_polling()
    return 0


if __name__ == "__main__":
    sys.exit(main())
