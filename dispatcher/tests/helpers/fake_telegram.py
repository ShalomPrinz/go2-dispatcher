"""Fake Telegram objects for calling bot handlers directly (docs/testing.md)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock


def fake_update(text: str | None, user_id: int | None = 1) -> SimpleNamespace:
    """``Update``-like: ``effective_user.id``, ``effective_message.text`` and an
    ``AsyncMock`` ``effective_message.reply_text``. ``user_id=None`` → no user."""
    user = None if user_id is None else SimpleNamespace(id=user_id)
    message = SimpleNamespace(text=text, reply_text=AsyncMock())
    return SimpleNamespace(effective_user=user, effective_message=message)


def fake_context(**bot_data: Any) -> SimpleNamespace:
    """``Context``-like with ``application.bot_data``."""
    return SimpleNamespace(application=SimpleNamespace(bot_data=dict(bot_data)))


def replies(update: SimpleNamespace) -> list[str]:
    """Texts passed to ``reply_text``, in order."""
    return [c.args[0] for c in update.effective_message.reply_text.await_args_list]

