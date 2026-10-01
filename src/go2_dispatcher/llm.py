"""LLM client and plan contract (§12): tool schema, AnthropicPlanner, infra retries."""

from __future__ import annotations

import math
import threading
import time
from typing import Any, Callable, Literal, Protocol

import anthropic
import httpx2  # the anthropic SDK (1.x) runs on httpx2; an injected client must be httpx2
from pydantic import BaseModel

from .config import LLMConfig
from .models import LLMInterrupted, LLMUnavailable, Plan
from .validation import validate_tool_input

__all__ = [
    "TOOL_NAME", "TOOL_REQUIRED", "STEP_REQUIRED", "plan_tool_schema",
    "LLMResult", "PlannerClient", "AnthropicPlanner",
    "RETRYABLE_STATUS_CODES", "RETRY_AFTER_CAP_S",
    "ERR_MAX_TOKENS", "ERR_NO_TOOL_CALL",
]

TOOL_NAME = "submit_plan"
TOOL_REQUIRED = ["status", "steps"]
STEP_REQUIRED = ["skill", "params"]

RETRYABLE_STATUS_CODES = frozenset({408, 409, 429})   # plus every status >= 500
RETRY_AFTER_CAP_S = 30.0

ERR_MAX_TOKENS = "reply was cut off; keep the plan shorter"
ERR_NO_TOOL_CALL = "no submit_plan call in reply"

RejectionKind = Literal["none", "schema", "horizon", "semantic", "no_tool_call", "max_tokens"]


# --- Tool schema (§12.2) ----------------------------------------------------------------


def plan_tool_schema(horizon: int) -> dict:
    """The hand-written submit_plan tool definition, with ``maxItems = horizon``."""
    return {
        "name": TOOL_NAME,
        "description": "Submit your plan for the current task. Call this exactly once.",
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["PLAN", "DONE", "ABORT"],
                    "description": "PLAN: run the steps. DONE: task complete, no steps. "
                                   "ABORT: cannot or should not be done, no steps.",
                },
                "steps": {
                    "type": "array",
                    "maxItems": horizon,
                    "description": "Skill calls to run in order. Empty for DONE and ABORT.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "skill": {"type": "string",
                                      "description": "Skill name from the skill list."},
                            "params": {"type": "object",
                                       "description": "Parameter values for this skill."},
                        },
                        "required": list(STEP_REQUIRED),
                        "additionalProperties": False,
                    },
                },
                "replan_after": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Optional. Stop after this step number and call again "
                                   "before running the rest.",
                },
                "message": {
                    "type": "string",
                    "description": "Required for DONE and ABORT: text for the operator. "
                                   "Optional for PLAN.",
                },
            },
            "required": list(TOOL_REQUIRED),
            "additionalProperties": False,
        },
    }


# --- Interfaces (§12.1) -----------------------------------------------------------------


class LLMResult(BaseModel):
    plan: Plan | None
    tool_input: Any | None               # raw tool input as received
    errors: list[str]                    # empty iff plan is valid
    rejection_kind: RejectionKind
    horizon_exceeded: bool
    usage: dict                          # response.usage.model_dump(), verbatim
    stop_reason: str | None
    content: list[dict]                  # all response content blocks, model_dump()
    latency_ms: float                    # successful attempt only
    total_ms: float                      # whole plan() call, incl. failed attempts and backoff
    attempts: int                        # 1 + infra retries
    response_id: str | None
    request_id: str | None


class PlannerClient(Protocol):
    def plan(self, *, system: list[str], user: str, tool_schema: dict, call_index: int,
             remaining_s: Callable[[], float], stop_event: threading.Event,
             on_infra_retry: Callable[[dict], None]) -> LLMResult: ...


def _default_wait(ev: threading.Event, s: float) -> bool:
    return ev.wait(s)


def _retry_after_s(e: Exception) -> float | None:
    """Numeric ``retry-after`` header of an error response, if any."""
    response = getattr(e, "response", None)
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


def _is_retryable(e: anthropic.APIError) -> bool:
    if isinstance(e, anthropic.APIConnectionError):     # includes APITimeoutError
        return True
    if isinstance(e, anthropic.APIStatusError):
        return e.status_code in RETRYABLE_STATUS_CODES or e.status_code >= 500
    return False


class AnthropicPlanner:
    """PlannerClient over the Anthropic Messages API, with its own infra retries (§12.3–§12.6)."""

    def __init__(self, api_key: str, llm_cfg: LLMConfig, horizon: int, *,
                 http_client: httpx2.Client | None = None,
                 wait: Callable[[threading.Event, float], bool] = _default_wait):
        self._cfg = llm_cfg
        self._horizon = horizon
        self._wait = wait
        self._client = anthropic.Anthropic(api_key=api_key, max_retries=0,
                                           http_client=http_client)

    def plan(self, *, system: list[str], user: str, tool_schema: dict, call_index: int,
             remaining_s: Callable[[], float], stop_event: threading.Event,
             on_infra_retry: Callable[[dict], None]) -> LLMResult:
        cfg = self._cfg
        t_start = time.monotonic()
        retries = 0
        while True:
            remaining = remaining_s()
            if remaining <= 0:
                raise LLMInterrupted("task_time_limit")
            if stop_event.is_set():
                raise LLMInterrupted("operator")
            attempt_timeout_s = min(cfg.request_timeout_s, remaining)
            t_attempt = time.monotonic()
            try:
                resp = self._client.with_options(timeout=attempt_timeout_s).messages.create(
                    model=cfg.model,
                    max_tokens=cfg.max_tokens,
                    system=[{"type": "text", "text": system[0]},
                            {"type": "text", "text": system[1]}],
                    tools=[tool_schema],
                    tool_choice={"type": "tool", "name": TOOL_NAME},
                    messages=[{"role": "user", "content": user}],
                    # SDK 1.x dropped the sampling kwargs; the API field is sent as-is
                    extra_body={"temperature": cfg.temperature},
                )
            except anthropic.APIError as e:
                attempt_latency_ms = (time.monotonic() - t_attempt) * 1000.0
                status_code = getattr(e, "status_code", None)
                if not _is_retryable(e) or retries >= cfg.infra_max_retries:
                    raise LLMUnavailable(
                        f"{type(e).__name__} {status_code or ''}".strip()) from e
                sleep_s = cfg.infra_backoff_s[retries]
                retry_after = _retry_after_s(e)
                if retry_after is not None:
                    sleep_s = min(max(sleep_s, retry_after), RETRY_AFTER_CAP_S)
                if sleep_s >= remaining_s():
                    raise LLMInterrupted("task_time_limit") from e
                on_infra_retry({
                    "call_index": call_index,
                    "attempt": retries + 1,
                    "error_type": type(e).__name__,
                    "status_code": status_code,
                    "attempt_latency_ms": attempt_latency_ms,
                    "sleep_s": sleep_s,
                })
                if self._wait(stop_event, sleep_s):
                    raise LLMInterrupted("operator") from e
                retries += 1
                continue
            latency_ms = (time.monotonic() - t_attempt) * 1000.0
            break

        return self._result(resp, latency_ms=latency_ms,
                            total_ms=(time.monotonic() - t_start) * 1000.0,
                            attempts=retries + 1)

    def _result(self, resp: Any, *, latency_ms: float, total_ms: float,
                attempts: int) -> LLMResult:
        """Response handling (§12.4)."""
        block = next((b for b in resp.content
                      if b.type == "tool_use" and b.name == TOOL_NAME), None)
        tool_input = block.input if block is not None else None
        plan: Plan | None = None
        horizon_exceeded = False
        if resp.stop_reason == "max_tokens":
            errors, kind = [ERR_MAX_TOKENS], "max_tokens"
        elif block is None:
            errors, kind = [ERR_NO_TOOL_CALL], "no_tool_call"
        else:
            plan, errors, horizon_exceeded, kind = validate_tool_input(tool_input, self._horizon)
        return LLMResult(
            plan=plan,
            tool_input=tool_input,
            errors=errors,
            rejection_kind=kind,
            horizon_exceeded=horizon_exceeded,
            usage=resp.usage.model_dump(),
            stop_reason=resp.stop_reason,
            content=[b.model_dump() for b in resp.content],
            latency_ms=latency_ms,
            total_ms=total_ms,
            attempts=attempts,
            response_id=resp.id,
            request_id=getattr(resp, "_request_id", None),
        )
