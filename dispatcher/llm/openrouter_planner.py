"""OpenRouterPlanner: the planner over OpenRouter's chat completions API via httpx (dispatcher/docs/llm.md)."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..config import LLMConfig
from .client import InfraRetry, LLMResult
from .retry import Failure, call_with_retries, default_wait, retry_after_s, retryable_status
from .surface import PromptSurface

if TYPE_CHECKING:
    import httpx

# `httpx` is imported only inside the planner and its helpers, like `anthropic` (dispatcher/docs/llm.md).

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterError(Exception):
    """A reply that is not a usable completion: an ``error`` body (HTTP 200 or not) or invalid JSON."""

    def __init__(self, status_code: int | None, response: httpx.Response):
        super().__init__(f"OpenRouter error {status_code}")
        self.status_code = status_code
        self.response = response


def _classify_openrouter(e: Exception) -> Failure | None:
    import httpx

    if isinstance(e, httpx.TransportError):  # connection errors and timeouts
        return Failure(type(e).__name__, None, True, None)
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        return Failure(type(e).__name__, code, retryable_status(code), retry_after_s(e.response))
    if isinstance(e, OpenRouterError):
        code = e.status_code
        return Failure(type(e).__name__, code, retryable_status(code), retry_after_s(e.response))
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
        wait: Callable[[threading.Event, float], bool] = default_wait,
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
            "tool_choice": {"type": "function", "function": {"name": self.surface.tool_schema["name"]}},
        }

    def plan(
        self,
        *,
        user: str,
        call_index: int,
        remaining_s: Callable[[], float],
        stop_event: threading.Event,
        on_infra_retry: Callable[[InfraRetry], None],
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
        data, latency_ms, attempts = call_with_retries(
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
        name = self.surface.tool_schema["name"]
        call = next((c for c in calls if (c.get("function") or {}).get("name") == name), None)
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
