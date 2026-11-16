"""AnthropicPlanner: the planner over the Anthropic Messages API (dispatcher/docs/llm.md)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..config import LLMConfig
from .client import InfraRetry, LLMResult
from .retry import Failure, call_with_retries, default_wait, retry_after_s, retryable_status
from .surface import PromptSurface

if TYPE_CHECKING:
    import httpx2  # the anthropic SDK (1.x) runs on httpx2; an injected client must be httpx2
    from anthropic.types import ThinkingConfigParam

# `anthropic` is imported only inside the planner and its helper: it costs about 0.8 s at start-up, and stub runs
# with the test planner, `catalog`, `state` and `--reset-stub` never call the API (dispatcher/docs/llm.md).


def _classify_anthropic(e: Exception) -> Failure | None:
    import anthropic

    if not isinstance(e, anthropic.APIError):
        return None
    status_code = getattr(e, "status_code", None)
    retryable = isinstance(e, anthropic.APIConnectionError) or (  # includes APITimeoutError
        isinstance(e, anthropic.APIStatusError) and retryable_status(status_code)
    )
    return Failure(type(e).__name__, status_code, retryable, retry_after_s(getattr(e, "response", None)))


class AnthropicPlanner:
    """PlannerClient over the Anthropic Messages API, with its own infra retries (dispatcher/docs/llm.md)."""

    def __init__(
        self,
        api_key: str,
        llm_cfg: LLMConfig,
        surface: PromptSurface,
        *,
        http_client: httpx2.Client | None = None,
        wait: Callable[[threading.Event, float], bool] = default_wait,
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
        on_infra_retry: Callable[[InfraRetry], None],
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
        resp, latency_ms, attempts = call_with_retries(
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
        name = self.surface.tool_schema["name"]
        block = next((b for b in resp.content if b.type == "tool_use" and b.name == name), None)
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
