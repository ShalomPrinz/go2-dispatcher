"""LLM client and plan contract (dispatcher/docs/llm.md): tool schema, AnthropicPlanner, OpenRouterPlanner,
infra retries."""

from __future__ import annotations

import json
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, ValidationError

from . import prompts
from .config import LLMConfig
from .models import LLMInterrupted, LLMUnavailable, Plan, StopCause

if TYPE_CHECKING:
    import httpx
    import httpx2  # the anthropic SDK (1.x) runs on httpx2; an injected client must be httpx2
    from anthropic.types import ThinkingConfigParam

    from .context import PromptSurface  # context imports this module at run time

# `anthropic` and `httpx` are imported only inside the planners and their helpers: `anthropic` costs about
# 0.8 s at start-up, and stub runs with the test planner, `catalog`, `state` and `--reset-stub` never
# call the API (dispatcher/docs/llm.md).

__all__ = [
    "TOOL_NAME",
    "TOOL_REQUIRED",
    "STEP_REQUIRED",
    "plan_tool_schema",
    "LLMResult",
    "PlanCheck",
    "check_reply",
    "PlannerClient",
    "AnthropicPlanner",
    "OpenRouterPlanner",
    "OPENROUTER_URL",
    "CUT_OFF_STOP_REASONS",
    "RETRYABLE_STATUS_CODES",
    "RETRY_AFTER_CAP_S",
    "validate_tool_input",
]

TOOL_NAME = "submit_plan"
TOOL_REQUIRED = ["status", "steps"]
STEP_REQUIRED = ["skill", "params"]

RETRYABLE_STATUS_CODES = frozenset({408, 409, 429})  # plus every status >= 500
RETRY_AFTER_CAP_S = 30.0
CUT_OFF_STOP_REASONS = frozenset({"max_tokens", "length"})  # Anthropic, OpenRouter (dispatcher/docs/llm.md)
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

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
        errors.append(prompts.PLAN_NEEDS_STEPS)
    if s in ("DONE", "ABORT"):
        if n > 0:
            errors.append(prompts.STATUS_NO_STEPS.format(status=s))
        if plan.message is None or not plan.message.strip():
            errors.append(prompts.STATUS_NEEDS_MESSAGE.format(status=s))
    if plan.replan_after is not None:
        if s != "PLAN":
            errors.append(prompts.REPLAN_ONLY_PLAN)
        elif not 1 <= plan.replan_after <= n:
            errors.append(prompts.REPLAN_OUT_OF_RANGE.format(n=n))
    return errors


def validate_tool_input(raw: Any, horizon: int) -> tuple[Plan | None, list[str], RejectionKind]:
    """Returns (plan, errors, rejection_kind). Plans are never truncated: an over-long plan is
    rejected as a whole."""
    errors: list[str] = []

    # 1. horizon, on the raw input, before pydantic
    kind: RejectionKind = "none"
    if isinstance(raw, dict) and isinstance(raw.get("steps"), list) and len(raw["steps"]) > horizon:
        kind = "horizon"
        errors.append(prompts.HORIZON_EXCEEDED.format(n=len(raw["steps"]), horizon=horizon))

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

    # 4. rejection kind: horizon first
    if kind == "none" and schema_errors:
        kind = "schema"
    elif kind == "none" and semantic_errors:
        kind = "semantic"

    # 5. a plan only if there are no errors at all
    return (plan if not errors else None), errors, kind


# --- Interfaces (dispatcher/docs/llm.md) -----------------------------------------------------------------


class LLMResult(BaseModel):
    """One planner reply, unvalidated: the loop checks it with ``check_reply`` (dispatcher/docs/llm.md)."""

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


class PlanCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: Plan | None  # set iff errors is empty
    errors: list[str]
    rejection_kind: RejectionKind


def check_reply(res: LLMResult, horizon: int) -> PlanCheck:
    """Response handling then plan validation, in the order of dispatcher/docs/llm.md."""
    if res.stop_reason in CUT_OFF_STOP_REASONS:
        return PlanCheck(plan=None, errors=[prompts.ERR_MAX_TOKENS], rejection_kind="max_tokens")
    if res.tool_input is None:
        return PlanCheck(plan=None, errors=[prompts.ERR_NO_TOOL_CALL], rejection_kind="no_tool_call")
    plan, errors, kind = validate_tool_input(res.tool_input, horizon)
    return PlanCheck(plan=plan, errors=errors, rejection_kind=kind)


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


def _default_wait(ev: threading.Event, s: float) -> bool:
    return ev.wait(s)


def _retry_after_s(response: Any) -> float | None:
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


def _retryable_status(status_code: int | None) -> bool:
    return status_code is not None and (status_code in RETRYABLE_STATUS_CODES or status_code >= 500)


@dataclass(frozen=True)
class _Failure:
    """One failed attempt, as the shared retry loop sees it."""

    error_type: str
    status_code: int | None
    retryable: bool
    retry_after_s: float | None


_T = TypeVar("_T")


def _call_with_retries(
    send: Callable[[float], _T],
    classify: Callable[[Exception], _Failure | None],
    *,
    cfg: LLMConfig,
    call_index: int,
    remaining_s: Callable[[], float],
    stop_event: threading.Event,
    on_infra_retry: Callable[[dict], None],
    wait: Callable[[threading.Event, float], bool],
) -> tuple[_T, float, int]:
    """Runs ``send(attempt_timeout_s)`` under the infra-retry rules (dispatcher/docs/llm.md).
    ``classify`` maps a provider error to a ``_Failure``; None re-raises it. Returns (reply, latency_ms, attempts)."""
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
                {
                    "call_index": call_index,
                    "attempt": retries + 1,
                    "error_type": failure.error_type,
                    "status_code": failure.status_code,
                    "attempt_latency_ms": attempt_latency_ms,
                    "sleep_s": sleep_s,
                }
            )
            if wait(stop_event, sleep_s):
                raise LLMInterrupted(StopCause.OPERATOR) from e
            retries += 1
            continue
        return reply, (time.monotonic() - t_attempt) * 1000.0, retries + 1


# --- Anthropic ---------------------------------------------------------------------------------------------


def _classify_anthropic(e: Exception) -> _Failure | None:
    import anthropic

    if not isinstance(e, anthropic.APIError):
        return None
    status_code = getattr(e, "status_code", None)
    retryable = isinstance(e, anthropic.APIConnectionError) or (  # includes APITimeoutError
        isinstance(e, anthropic.APIStatusError) and _retryable_status(status_code)
    )
    return _Failure(type(e).__name__, status_code, retryable, _retry_after_s(getattr(e, "response", None)))


class AnthropicPlanner:
    """PlannerClient over the Anthropic Messages API, with its own infra retries (dispatcher/docs/llm.md)."""

    def __init__(
        self,
        api_key: str,
        llm_cfg: LLMConfig,
        surface: PromptSurface,
        *,
        http_client: httpx2.Client | None = None,
        wait: Callable[[threading.Event, float], bool] = _default_wait,
    ):
        self._cfg = llm_cfg
        self.surface = surface
        self._wait = wait
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key, max_retries=0, http_client=http_client)

    def plan(
        self,
        *,
        user: str,
        call_index: int,
        remaining_s: Callable[[], float],
        stop_event: threading.Event,
        on_infra_retry: Callable[[dict], None],
    ) -> LLMResult:
        from anthropic.types import ToolParam

        cfg = self._cfg
        system_text, skills_text = self.surface.system
        thinking: ThinkingConfigParam = (
            {"type": "adaptive"} if cfg.thinking == "adaptive" else {"type": "between_tools"}
        )

        def send(timeout_s: float) -> Any:
            return self._client.with_options(timeout=timeout_s).messages.create(
                model=cfg.model,
                max_tokens=cfg.max_tokens,
                system=[{"type": "text", "text": system_text}, {"type": "text", "text": skills_text}],
                tools=[ToolParam(**self.surface.tool_schema)],
                tool_choice={"type": "auto"},
                thinking=thinking,
                messages=[{"role": "user", "content": user}],
            )

        t_start = time.monotonic()
        resp, latency_ms, attempts = _call_with_retries(
            send,
            _classify_anthropic,
            cfg=cfg,
            call_index=call_index,
            remaining_s=remaining_s,
            stop_event=stop_event,
            on_infra_retry=on_infra_retry,
            wait=self._wait,
        )
        return self._result(
            resp, latency_ms=latency_ms, total_ms=(time.monotonic() - t_start) * 1000.0, attempts=attempts
        )

    def _result(self, resp: Any, *, latency_ms: float, total_ms: float, attempts: int) -> LLMResult:
        """Takes the first submit_plan tool_use block, skipping thinking and text blocks; all blocks are kept
        in ``content`` (dispatcher/docs/llm.md)."""
        block = next((b for b in resp.content if b.type == "tool_use" and b.name == TOOL_NAME), None)
        return LLMResult(
            tool_input=block.input if block is not None else None,
            usage=resp.usage.model_dump(),
            stop_reason=resp.stop_reason,
            content=[b.model_dump() for b in resp.content],
            latency_ms=latency_ms,
            total_ms=total_ms,
            attempts=attempts,
            response_id=resp.id,
            request_id=getattr(resp, "_request_id", None),
        )


# --- OpenRouter --------------------------------------------------------------------------------------------


class OpenRouterError(Exception):
    """A reply that is not a usable completion: an ``error`` body (HTTP 200 or not) or invalid JSON."""

    def __init__(self, status_code: int | None, response: httpx.Response):
        super().__init__(f"OpenRouter error {status_code}")
        self.status_code = status_code
        self.response = response


def _classify_openrouter(e: Exception) -> _Failure | None:
    import httpx

    if isinstance(e, httpx.TransportError):  # connection errors and timeouts
        return _Failure(type(e).__name__, None, True, None)
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        return _Failure(type(e).__name__, code, _retryable_status(code), _retry_after_s(e.response))
    if isinstance(e, OpenRouterError):
        code = e.status_code
        return _Failure(type(e).__name__, code, _retryable_status(code), _retry_after_s(e.response))
    return None


def _openrouter_tool(schema: dict) -> dict:
    """The submit_plan definition as an OpenAI-style function tool; same name, description and schema."""
    return {
        "type": "function",
        "function": {
            "name": schema["name"],
            "description": schema["description"],
            "parameters": schema["input_schema"],
        },
    }


def _tool_arguments(raw: Any) -> Any:
    """Function arguments arrive as a JSON string; unparseable text is kept raw and fails schema validation."""
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except ValueError:
        return raw


class OpenRouterPlanner:
    """PlannerClient over OpenRouter's chat completions API via httpx, with its own infra retries
    (dispatcher/docs/llm.md)."""

    def __init__(
        self,
        api_key: str,
        llm_cfg: LLMConfig,
        surface: PromptSurface,
        *,
        http_client: httpx.Client | None = None,
        wait: Callable[[threading.Event, float], bool] = _default_wait,
    ):
        import httpx

        self._cfg = llm_cfg
        self.surface = surface
        self._wait = wait
        self._client = http_client or httpx.Client()
        self._headers = {"Authorization": f"Bearer {api_key}"}

    def _body(self, user: str) -> dict:
        system_text, skills_text = self.surface.system
        return {
            "model": self._cfg.model,
            "max_tokens": self._cfg.max_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": [{"type": "text", "text": system_text}, {"type": "text", "text": skills_text}],
                },
                {"role": "user", "content": user},
            ],
            "tools": [_openrouter_tool(self.surface.tool_schema)],
            "tool_choice": "auto",
        }

    def plan(
        self,
        *,
        user: str,
        call_index: int,
        remaining_s: Callable[[], float],
        stop_event: threading.Event,
        on_infra_retry: Callable[[dict], None],
    ) -> LLMResult:
        body = self._body(user)

        def send(timeout_s: float) -> dict:
            resp = self._client.post(OPENROUTER_URL, json=body, headers=self._headers, timeout=timeout_s)
            resp.raise_for_status()
            try:
                data = resp.json()
            except ValueError:
                raise OpenRouterError(None, resp) from None
            if not isinstance(data, dict) or "choices" not in data:
                error = data.get("error") if isinstance(data, dict) else None
                code = error.get("code") if isinstance(error, dict) else None
                raise OpenRouterError(code if isinstance(code, int) else None, resp)
            return data

        t_start = time.monotonic()
        data, latency_ms, attempts = _call_with_retries(
            send,
            _classify_openrouter,
            cfg=self._cfg,
            call_index=call_index,
            remaining_s=remaining_s,
            stop_event=stop_event,
            on_infra_retry=on_infra_retry,
            wait=self._wait,
        )
        return self._result(
            data, latency_ms=latency_ms, total_ms=(time.monotonic() - t_start) * 1000.0, attempts=attempts
        )

    def _result(self, data: dict, *, latency_ms: float, total_ms: float, attempts: int) -> LLMResult:
        """Takes the first submit_plan tool call of the first choice; all choices are kept verbatim in ``content``
        (dispatcher/docs/llm.md)."""
        choices = data.get("choices") or []
        first = choices[0] if choices else {}
        calls = (first.get("message") or {}).get("tool_calls") or []
        call = next((c for c in calls if (c.get("function") or {}).get("name") == TOOL_NAME), None)
        return LLMResult(
            tool_input=_tool_arguments(call["function"].get("arguments")) if call is not None else None,
            usage=data.get("usage") or {},
            stop_reason=first.get("finish_reason"),
            content=choices,
            latency_ms=latency_ms,
            total_ms=total_ms,
            attempts=attempts,
            response_id=data.get("id"),
            request_id=None,
        )
