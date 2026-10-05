"""OpenRouterPlanner over httpx.MockTransport — no network (dispatcher/docs/llm.md, tests/docs/testing.md)."""

from __future__ import annotations

import json
import threading

import httpx
import pytest

from dispatcher import prompts
from dispatcher.context import PromptSurface
from dispatcher.llm import OPENROUTER_URL, TOOL_NAME, OpenRouterPlanner, check_reply, plan_tool_schema
from dispatcher.models import LLMUnavailable
from dispatcher.tests.helpers import make_config

H = 5
SYSTEM = ("system one", "## Skills\ncatalog")
SURFACE = PromptSurface(
    system=SYSTEM, catalog_text="catalog", tool_schema=plan_tool_schema(H), registry_hash="0" * 16, skills=("sit",)
)
USER = "## Task\nsit down"
USAGE = {"prompt_tokens": 321, "completion_tokens": 45, "total_tokens": 366}
GOOD_INPUT = {"status": "PLAN", "steps": [{"skill": "sit", "params": {}}]}


def tool_call(inp, name=TOOL_NAME):
    return {"id": "call_1", "type": "function", "function": {"name": name, "arguments": json.dumps(inp)}}


def completion(message, finish_reason="tool_calls"):
    choice = {"index": 0, "finish_reason": finish_reason, "native_finish_reason": "x", "message": message}
    return {"id": "gen-1", "model": "m", "choices": [choice], "usage": USAGE}


def ok(body=None):
    body = body or completion({"role": "assistant", "content": None, "tool_calls": [tool_call(GOOD_INPUT)]})
    return httpx.Response(200, json=body)


class Harness:
    """Scripted HTTP replies, recorded requests, sleeps and infra-retry callbacks."""

    def __init__(self, tmp_path, replies):
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []
        self.retries: list[dict] = []
        cfg = make_config(tmp_path, llm={"provider": "openrouter", "model": "vendor/free-model"})
        client = httpx.Client(transport=httpx.MockTransport(self._handle))
        self.planner = OpenRouterPlanner("test-key", cfg.llm, SURFACE, http_client=client, wait=self._wait)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.replies.pop(0)

    def _wait(self, ev, s):
        self.sleeps.append(s)
        return False

    def plan(self):
        return self.planner.plan(
            user=USER,
            call_index=3,
            remaining_s=lambda: 1000.0,
            stop_event=threading.Event(),
            on_infra_retry=self.retries.append,
        )


def test_tool_call_parsed_and_request_shape(tmp_path):
    h = Harness(tmp_path, [ok()])
    r = h.plan()
    req = h.requests[0]
    assert str(req.url) == OPENROUTER_URL and req.headers["authorization"] == "Bearer test-key"
    body = json.loads(req.content)
    assert body["model"] == "vendor/free-model" and body["max_tokens"] == 2048
    assert body["tool_choice"] == "auto"
    assert body["messages"] == [
        {"role": "system", "content": [{"type": "text", "text": SYSTEM[0]}, {"type": "text", "text": SYSTEM[1]}]},
        {"role": "user", "content": USER},
    ]
    schema = plan_tool_schema(H)
    assert body["tools"] == [
        {
            "type": "function",
            "function": {"name": TOOL_NAME, "description": schema["description"], "parameters": schema["input_schema"]},
        }
    ]
    assert set(body) == {"model", "max_tokens", "messages", "tools", "tool_choice"}
    assert r.tool_input == GOOD_INPUT and r.usage == USAGE and r.stop_reason == "tool_calls"
    assert r.content == json.loads(ok().content)["choices"] and r.response_id == "gen-1" and r.attempts == 1
    c = check_reply(r, H)
    assert c.rejection_kind == "none" and c.plan is not None


def test_no_tool_call(tmp_path):
    r = Harness(tmp_path, [ok(completion({"role": "assistant", "content": "I would sit."}, "stop"))]).plan()
    assert r.tool_input is None
    c = check_reply(r, H)
    assert c.rejection_kind == "no_tool_call" and c.errors == [prompts.ERR_NO_TOOL_CALL]


def test_length_is_max_tokens(tmp_path):
    msg = {"role": "assistant", "content": None, "tool_calls": [tool_call({"status": "PLAN"})]}
    r = Harness(tmp_path, [ok(completion(msg, "length"))]).plan()
    assert r.stop_reason == "length"
    c = check_reply(r, H)
    assert c.rejection_kind == "max_tokens" and c.errors == [prompts.ERR_MAX_TOKENS]


@pytest.mark.parametrize("status", [429, 503])
def test_retryable_status_then_ok(tmp_path, status):
    h = Harness(tmp_path, [httpx.Response(status, json={"error": {}}, headers={"retry-after": "2"}), ok()])
    r = h.plan()
    assert r.attempts == 2 and h.sleeps == [2.0]
    assert h.retries[0]["status_code"] == status and h.retries[0]["error_type"] == "HTTPStatusError"


def test_error_body_with_200_is_retried(tmp_path):
    h = Harness(tmp_path, [httpx.Response(200, json={"error": {"code": 502, "message": "upstream"}}), ok()])
    assert h.plan().attempts == 2
    assert h.retries[0]["error_type"] == "OpenRouterError" and h.retries[0]["status_code"] == 502


def test_non_retryable_4xx(tmp_path):
    h = Harness(tmp_path, [httpx.Response(400, json={"error": {"code": 400}})])
    with pytest.raises(LLMUnavailable) as ei:
        h.plan()
    assert ei.value.detail == "HTTPStatusError 400" and len(h.requests) == 1 and h.retries == []
