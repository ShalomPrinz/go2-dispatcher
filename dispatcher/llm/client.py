"""The planner protocol, its reply and its errors (dispatcher/docs/llm.md)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from ..models import StopCause
from .surface import PromptSurface

CUT_OFF_STOP_REASONS = frozenset({"max_tokens", "length"})  # Anthropic, OpenRouter (dispatcher/docs/llm.md)


class LLMUnavailable(Exception):
    """Infra retries exhausted, or a non-retryable API error."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class LLMInterrupted(Exception):
    """Stop (``OPERATOR``) or task deadline (``TASK_TIME_LIMIT``) hit while waiting to retry an LLM call."""

    def __init__(self, cause: StopCause):
        super().__init__(cause)
        self.cause = StopCause(cause)


class LLMResult(BaseModel):
    """One planner reply, unvalidated: the loop checks it with ``planning.validation.check_reply``
    (dispatcher/docs/llm.md)."""

    model_config = ConfigDict(extra="forbid")

    tool_input: Any | None  # raw submit_plan input as received; None if the reply has no submit_plan call
    usage: dict  # the provider's usage object, verbatim
    stop_reason: str | None  # Anthropic stop_reason or OpenRouter finish_reason, verbatim
    content: list[dict]  # Anthropic: all content blocks; OpenRouter: all choices; verbatim
    latency_ms: float  # successful attempt only
    total_ms: float  # whole plan() call, incl. failed attempts and backoff
    attempts: int  # 1 + infra retries
    response_id: str | None
    request_id: str | None

    @property
    def cut_off(self) -> bool:
        """The reply hit the token limit (``CUT_OFF_STOP_REASONS``)."""
        return self.stop_reason in CUT_OFF_STOP_REASONS


class PlannerClient(Protocol):
    """Sends the fixed ``surface`` it was built with plus a per-call user message (dispatcher/docs/llm.md)."""

    @property
    def surface(self) -> PromptSurface: ...

    def plan(
        self,
        *,
        user: str,
        call_index: int,
        remaining_s: Callable[[], float],
        stop_event: threading.Event,
        on_infra_retry: Callable[[dict], None],
    ) -> LLMResult: ...
