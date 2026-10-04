"""ScriptedPlanner: a PlannerClient that replays scripted replies (tests/docs/testing.md)."""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from dispatcher.llm import TOOL_NAME, LLMResult
from dispatcher.models import Plan

if TYPE_CHECKING:
    from dispatcher.context import PromptSurface

SCRIPTED_USAGE = {"input_tokens": 100, "output_tokens": 20}


class ScriptedPlanner:
    """Returns ``items`` in order, one per ``plan()`` call:

    - a ``Plan`` -> its dump as raw tool input (the loop re-validates it);
    - a ``dict`` -> raw tool input (the loop validates it);
    - an exception instance -> raised.

    ``surface`` is the fixed request part it reports (it sends nothing).
    ``calls`` records every call's kwargs. ``on_call(call_index)`` runs before returning
    or raising. Calling more times than scripted raises ``AssertionError``.
    """

    def __init__(
        self,
        items: Sequence[Plan | dict | BaseException],
        surface: PromptSurface,
        on_call: Callable[[int], None] | None = None,
    ):
        self.items = list(items)
        self.surface = surface
        self.on_call = on_call
        self.calls: list[dict[str, Any]] = []

    @property
    def remaining(self) -> int:
        return len(self.items) - len(self.calls)

    def plan(
        self,
        *,
        user: str,
        call_index: int,
        remaining_s: Callable[[], float],
        stop_event: threading.Event,
        on_infra_retry: Callable[[dict], None],
    ) -> LLMResult:
        kwargs = {
            "user": user,
            "call_index": call_index,
            "remaining_s": remaining_s,
            "stop_event": stop_event,
            "on_infra_retry": on_infra_retry,
        }
        n = len(self.calls)
        self.calls.append(kwargs)
        if n >= len(self.items):
            raise AssertionError(f"ScriptedPlanner called {n + 1} times; only {len(self.items)} replies scripted")
        item = self.items[n]
        if self.on_call is not None:
            self.on_call(call_index)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, Plan):
            tool_input: Any = item.model_dump(exclude_none=True)
        elif isinstance(item, dict):
            tool_input = item
        else:
            raise TypeError(f"unsupported scripted item: {item!r}")
        return LLMResult(
            tool_input=tool_input,
            usage=dict(SCRIPTED_USAGE),
            stop_reason="tool_use",
            content=[{"type": "tool_use", "id": f"toolu_scripted_{n + 1}", "name": TOOL_NAME, "input": tool_input}],
            latency_ms=0.0,
            total_ms=0.0,
            attempts=1,
            response_id=f"msg_scripted_{n + 1}",
            request_id=None,
        )
