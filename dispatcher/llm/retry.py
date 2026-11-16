"""The infra-retry loop shared by the providers (dispatcher/docs/llm.md)."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from ..config import LLMConfig
from ..models import StopCause
from .client import InfraRetry, LLMInterrupted, LLMUnavailable

RETRYABLE_STATUS_CODES = frozenset({408, 409, 429})  # plus every status >= 500
RETRY_AFTER_CAP_S = 30.0


def default_wait(ev: threading.Event, s: float) -> bool:
    return ev.wait(s)


def retry_after_s(response: Any) -> float | None:
    """Numeric ``retry-after`` header of an error response, if any."""
    if response is None:
        return None
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        s = float(value)
    except ValueError:
        return None
    return s if math.isfinite(s) and s >= 0 else None


def retryable_status(status_code: int | None) -> bool:
    return status_code is not None and (status_code in RETRYABLE_STATUS_CODES or status_code >= 500)


@dataclass(frozen=True)
class Failure:
    """One failed attempt, as the shared retry loop sees it."""

    error_type: str
    status_code: int | None
    retryable: bool
    retry_after_s: float | None


_T = TypeVar("_T")


def call_with_retries(
    send: Callable[[float], _T],
    classify: Callable[[Exception], Failure | None],
    *,
    cfg: LLMConfig,
    call_index: int,
    remaining_s: Callable[[], float],
    stop_event: threading.Event,
    on_infra_retry: Callable[[InfraRetry], None],
    wait: Callable[[threading.Event, float], bool],
) -> tuple[_T, float, int]:
    """Runs ``send(attempt_timeout_s)`` under the infra-retry rules (dispatcher/docs/llm.md).
    ``classify`` maps a provider error to a ``Failure``; None re-raises it. Returns (reply, latency_ms, attempts)."""
    retries = 0
    while True:
        remaining = remaining_s()
        if remaining <= 0:
            raise LLMInterrupted(StopCause.TASK_TIME_LIMIT)
        if stop_event.is_set():
            raise LLMInterrupted(StopCause.OPERATOR)
        t_attempt = time.monotonic()
        try:
            reply = send(min(cfg.request_timeout_s, remaining))
        except Exception as e:
            failure = classify(e)
            if failure is None:
                raise
            attempt_latency_ms = (time.monotonic() - t_attempt) * 1000.0
            if not failure.retryable or retries >= len(cfg.infra_backoff_s):
                raise LLMUnavailable(f"{failure.error_type} {failure.status_code or ''}".strip()) from e
            sleep_s = cfg.infra_backoff_s[retries]
            if failure.retry_after_s is not None:
                sleep_s = min(max(sleep_s, failure.retry_after_s), RETRY_AFTER_CAP_S)
            if sleep_s >= remaining_s():
                raise LLMInterrupted(StopCause.TASK_TIME_LIMIT) from e
            on_infra_retry(
                InfraRetry(
                    call_index=call_index,
                    attempt=retries + 1,
                    error_type=failure.error_type,
                    status_code=failure.status_code,
                    attempt_latency_ms=attempt_latency_ms,
                    sleep_s=sleep_s,
                )
            )
            if wait(stop_event, sleep_s):
                raise LLMInterrupted(StopCause.OPERATOR) from e
            retries += 1
            continue
        return reply, (time.monotonic() - t_attempt) * 1000.0, retries + 1
