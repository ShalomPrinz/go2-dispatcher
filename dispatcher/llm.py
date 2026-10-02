"""LLM client and plan contract (dispatcher/docs/llm.md): tool schema, AnthropicPlanner, infra retries."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal, Protocol

from pydantic import BaseModel, ValidationError

from .config import LLMConfig
from .models import LLMInterrupted, LLMUnavailable, Plan

if TYPE_CHECKING:
    import anthropic
    import httpx2  # the anthropic SDK (1.x) runs on httpx2; an injected client must be httpx2

# `anthropic` is imported only inside AnthropicPlanner and the retry helper: it costs about 0.8 s
# at start-up, and stub runs with the test planner, `catalog`, `state` and `--reset-stub` never
# call the API (dispatcher/docs/llm.md).

__all__ = [
    "TOOL_NAME",
    "TOOL_REQUIRED",
    "STEP_REQUIRED",
    "plan_tool_schema",
    "LLMResult",
    "PlannerClient",
    "AnthropicPlanner",
    "RETRYABLE_STATUS_CODES",
    "RETRY_AFTER_CAP_S",
    "ERR_MAX_TOKENS",
    "ERR_NO_TOOL_CALL",
    "validate_tool_input",
]

TOOL_NAME = "submit_plan"
TOOL_REQUIRED = ["status", "steps"]
STEP_REQUIRED = ["skill", "params"]

RETRYABLE_STATUS_CODES = frozenset({408, 409, 429})  # plus every status >= 500
RETRY_AFTER_CAP_S = 30.0

ERR_MAX_TOKENS = "reply was cut off; keep the plan shorter"
ERR_NO_TOOL_CALL = "no submit_plan call in reply"

RejectionKind = Literal["none", "schema", "horizon", "semantic", "no_tool_call", "max_tokens"]

# loc label for a pydantic error that has no location (the whole input is wrong)
ROOT_LOC = "input"


# --- Tool schema (dispatcher/docs/loop-and-context.md) ----------------------------------------------------------------


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
                            "skill": {"type": "string", "description": "Skill name from the skill list."},
                            "params": {"type": "object", "description": "Parameter values for this skill."},
                        },
                        "required": list(STEP_REQUIRED),
                        "additionalProperties": False,
                    },
                },
                "replan_after": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Optional. Stop after this step number and call again before running the rest.",
                },
                "message": {
                    "type": "string",
                    "description": "Required for DONE and ABORT: text for the operator. Optional for PLAN.",
                },
            },
            "required": list(TOOL_REQUIRED),
            "additionalProperties": False,
        },
    }


# --- Plan validation (dispatcher/docs/loop-and-context.md) -------------------------------------------------


def _schema_errors(e: ValidationError) -> list[str]:
    out: list[str] = []
    for err in e.errors():
        loc = ".".join(str(p) for p in err["loc"]) or ROOT_LOC
        out.append(f"{loc}: {err['msg']}")
    return out


def _semantic_errors(plan: Plan) -> list[str]:
    errors: list[str] = []
    s = plan.status
    n = len(plan.steps)
    if s == "PLAN" and n == 0:
        errors.append("status PLAN needs at least one step")
    if s in ("DONE", "ABORT"):
        if n > 0:
            errors.append(f"status {s} must have no steps")
        if plan.message is None or not plan.message.strip():
            errors.append(f"status {s} needs a message")
    if plan.replan_after is not None:
        if s != "PLAN":
            errors.append("replan_after is only allowed with status PLAN")
        elif not 1 <= plan.replan_after <= n:
            errors.append(f"replan_after must be between 1 and {n}")
    return errors


def validate_tool_input(raw: Any, horizon: int) -> tuple[Plan | None, list[str], RejectionKind]:
    """Returns (plan, errors, rejection_kind). Plans are never truncated: an over-long plan is
    rejected as a whole."""
    errors: list[str] = []

    # 1. horizon, on the raw input, before pydantic
    horizon_exceeded = False
    if isinstance(raw, dict) and isinstance(raw.get("steps"), list) and len(raw["steps"]) > horizon:
        horizon_exceeded = True
        errors.append(f"plan has {len(raw['steps'])} steps; the maximum is {horizon}")

    # 2. schema
    plan: Plan | None = None
    schema_errors: list[str] = []
    try:
        plan = Plan.model_validate(raw)
    except ValidationError as e:
        schema_errors = _schema_errors(e)
    errors.extend(schema_errors)

    # 3. semantic, only if the schema passed
    semantic_errors = _semantic_errors(plan) if plan is not None else []
    errors.extend(semantic_errors)

    # 5. rejection kind
    if horizon_exceeded:
        kind = "horizon"
    elif schema_errors:
        kind = "schema"
    elif semantic_errors:
        kind = "semantic"
    else:
        kind = "none"

    # 4. a plan only if there are no errors at all
    return (plan if not errors else None), errors, kind


# --- Interfaces (dispatcher/docs/llm.md) -----------------------------------------------------------------


class LLMResult(BaseModel):
    plan: Plan | None
    tool_input: Any | None  # raw tool input as received
    errors: list[str]  # empty iff plan is valid
    rejection_kind: RejectionKind
    usage: dict  # response.usage.model_dump(), verbatim
    stop_reason: str | None
    content: list[dict]  # all response content blocks, model_dump()
    latency_ms: float  # successful attempt only
    total_ms: float  # whole plan() call, incl. failed attempts and backoff
    attempts: int  # 1 + infra retries
    response_id: str | None
    request_id: str | None


class PlannerClient(Protocol):
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
    ) -> LLMResult: ...


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
    import anthropic

    if isinstance(e, anthropic.APIConnectionError):  # includes APITimeoutError
        return True
    if isinstance(e, anthropic.APIStatusError):
        return e.status_code in RETRYABLE_STATUS_CODES or e.status_code >= 500
    return False


class AnthropicPlanner:
    """PlannerClient over the Anthropic Messages API, with its own infra retries (dispatcher/docs/llm.md)."""

    def __init__(
        self,
        api_key: str,
        llm_cfg: LLMConfig,
        horizon: int,
        *,
        http_client: httpx2.Client | None = None,
        wait: Callable[[threading.Event, float], bool] = _default_wait,
    ):
        self._cfg = llm_cfg
        self._horizon = horizon
        self._wait = wait
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key, max_retries=0, http_client=http_client)

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
        import anthropic

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
                    system=[{"type": "text", "text": system[0]}, {"type": "text", "text": system[1]}],
                    tools=[tool_schema],
                    tool_choice={"type": "auto"},
                    thinking={"type": cfg.thinking},
                    messages=[{"role": "user", "content": user}],
                )
            except anthropic.APIError as e:
                attempt_latency_ms = (time.monotonic() - t_attempt) * 1000.0
                status_code = getattr(e, "status_code", None)
                if not _is_retryable(e) or retries >= len(cfg.infra_backoff_s):
                    raise LLMUnavailable(f"{type(e).__name__} {status_code or ''}".strip()) from e
                sleep_s = cfg.infra_backoff_s[retries]
                retry_after = _retry_after_s(e)
                if retry_after is not None:
                    sleep_s = min(max(sleep_s, retry_after), RETRY_AFTER_CAP_S)
                if sleep_s >= remaining_s():
                    raise LLMInterrupted("task_time_limit") from e
                on_infra_retry(
                    {
                        "call_index": call_index,
                        "attempt": retries + 1,
                        "error_type": type(e).__name__,
                        "status_code": status_code,
                        "attempt_latency_ms": attempt_latency_ms,
                        "sleep_s": sleep_s,
                    }
                )
                if self._wait(stop_event, sleep_s):
                    raise LLMInterrupted("operator") from e
                retries += 1
                continue
            latency_ms = (time.monotonic() - t_attempt) * 1000.0
            break

        return self._result(
            resp, latency_ms=latency_ms, total_ms=(time.monotonic() - t_start) * 1000.0, attempts=retries + 1
        )

    def _result(self, resp: Any, *, latency_ms: float, total_ms: float, attempts: int) -> LLMResult:
        """Response handling (dispatcher/docs/llm.md). Thinking and text blocks are skipped when choosing the
        tool_use block; all blocks are kept in ``content``."""
        block = next((b for b in resp.content if b.type == "tool_use" and b.name == TOOL_NAME), None)
        tool_input = block.input if block is not None else None
        plan: Plan | None = None
        if resp.stop_reason == "max_tokens":
            errors, kind = [ERR_MAX_TOKENS], "max_tokens"
        elif block is None:
            errors, kind = [ERR_NO_TOOL_CALL], "no_tool_call"
        else:
            plan, errors, kind = validate_tool_input(tool_input, self._horizon)
        return LLMResult(
            plan=plan,
            tool_input=tool_input,
            errors=errors,
            rejection_kind=kind,
            usage=resp.usage.model_dump(),
            stop_reason=resp.stop_reason,
            content=[b.model_dump() for b in resp.content],
            latency_ms=latency_ms,
            total_ms=total_ms,
            attempts=attempts,
            response_id=resp.id,
            request_id=getattr(resp, "_request_id", None),
        )
