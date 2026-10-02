"""ScriptedPlanner: a PlannerClient that replays scripted replies (docs/testing.md)."""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from typing import Any

from dispatcher.llm import TOOL_NAME, LLMResult
from dispatcher.models import Plan
from dispatcher.validation import validate_tool_input

SCRIPTED_USAGE = {"input_tokens": 100, "output_tokens": 20}


def _horizon_of(tool_schema: dict) -> int:
    return tool_schema["input_schema"]["properties"]["steps"]["maxItems"]


class ScriptedPlanner:
    """Returns ``items`` in order, one per ``plan()`` call:

    - a ``Plan`` -> a valid ``LLMResult`` (not re-validated);
    - a ``dict`` -> raw tool input, validated with ``validate_tool_input`` against the
      horizon in the ``tool_schema`` kwarg (``maxItems``);
    - an exception instance -> raised.

    ``calls`` records every call's kwargs. ``on_call(call_index)`` runs before returning
    or raising. Calling more times than scripted raises ``AssertionError``.
    """

    def __init__(self, items: Sequence[Plan | dict | BaseException], on_call: Callable[[int], None] | None = None):
        self.items = list(items)
        self.on_call = on_call
        self.calls: list[dict[str, Any]] = []

    @property
    def remaining(self) -> int:
        return len(self.items) - len(self.calls)

    def plan(
        self,
        *,
        system: list[str],
        user: str,
        tool_schema: dict,
        call_index: int,
        remaining_s: Callable[[], float],
        stop_event: threading.Event,
        on_infra_retry: Callable[[dict], None],
    ) -> LLMResult:
        kwargs = {
            "system": system,
            "user": user,
            "tool_schema": tool_schema,
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
            plan: Plan | None = item
            tool_input: Any = item.model_dump(exclude_none=True)
            errors: list[str] = []
            horizon_exceeded, kind = False, "none"
        elif isinstance(item, dict):
            tool_input = item
            plan, errors, horizon_exceeded, kind = validate_tool_input(item, _horizon_of(tool_schema))
        else:
            raise TypeError(f"unsupported scripted item: {item!r}")
        return LLMResult(
            plan=plan,
            tool_input=tool_input,
            errors=errors,
            rejection_kind=kind,
            horizon_exceeded=horizon_exceeded,
            usage=dict(SCRIPTED_USAGE),
            stop_reason="tool_use",
            content=[{"type": "tool_use", "id": f"toolu_scripted_{n + 1}", "name": TOOL_NAME, "input": tool_input}],
            latency_ms=0.0,
            total_ms=0.0,
            attempts=1,
            response_id=f"msg_scripted_{n + 1}",
            request_id=None,
        )
