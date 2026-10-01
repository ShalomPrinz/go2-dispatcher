"""AnthropicPlanner over httpx.MockTransport — no network (docs/llm.md, testing.md)."""

from __future__ import annotations

import json
import threading

import httpx2 as httpx  # the SDK's HTTP library (see llm.py)
import pytest
from helpers import make_config

from go2_dispatcher.llm import (
    ERR_MAX_TOKENS,
    ERR_NO_TOOL_CALL,
    TOOL_NAME,
    AnthropicPlanner,
    plan_tool_schema,
)
from go2_dispatcher.models import LLMInterrupted, LLMUnavailable

H = 5
SYSTEM = ["system one", "## Skills\ncatalog"]
USER = "## Task\nsit down"
USAGE = {"input_tokens": 321, "output_tokens": 45}
GOOD_INPUT = {"status": "PLAN", "steps": [{"skill": "sit", "params": {}}]}


def message(content, stop_reason="tool_use", usage=USAGE):
    return {"id": "msg_01", "type": "message", "role": "assistant", "model": "m",
            "content": content, "stop_reason": stop_reason, "stop_sequence": None,
            "usage": usage}


def tool_use(inp, name=TOOL_NAME, id_="toolu_1"):
    return {"type": "tool_use", "id": id_, "name": name, "input": inp}


def ok(body=None):
    return httpx.Response(200, json=body or message([tool_use(GOOD_INPUT)]),
                          headers={"request-id": "req_123"})


class Harness:
    """Scripted HTTP replies, recorded requests, sleeps and infra-retry callbacks."""

    def __init__(self, tmp_path, replies, *, wait_result=False, llm=None):
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []
        self.retries: list[dict] = []
        self.stop_event = threading.Event()
        self.wait_result = wait_result
        cfg = make_config(tmp_path, llm=llm or {})
        transport = httpx.MockTransport(self._handle)
        self.planner = AnthropicPlanner("test-key", cfg.llm, H,
                                        http_client=httpx.Client(transport=transport),
                                        wait=self._wait)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert self.replies, "more requests than scripted replies"
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def _wait(self, ev, s):
        self.sleeps.append(s)
        return self.wait_result

    def plan(self, remaining=1000.0):
        return self.planner.plan(system=SYSTEM, user=USER, tool_schema=plan_tool_schema(H),
                                 call_index=7, remaining_s=lambda: remaining,
                                 stop_event=self.stop_event,
                                 on_infra_retry=self.retries.append)


def test_request_body(tmp_path):
    h = Harness(tmp_path, [ok()])
    h.plan()
    assert len(h.requests) == 1
    req = h.requests[0]
    assert req.url.path == "/v1/messages"
    body = json.loads(req.content)
    assert body["tool_choice"] == {"type": "auto"}
    assert body["thinking"] == {"type": "between_tools"}
    assert body["system"] == [{"type": "text", "text": SYSTEM[0]},
                              {"type": "text", "text": SYSTEM[1]}]
    assert body["messages"] == [{"role": "user", "content": USER}]
    assert body["tools"] == [plan_tool_schema(H)]
    assert "strict" not in json.dumps(body)
    assert not body.get("stream")
    assert "temperature" not in json.dumps(body) and "extra_body" not in body
    cfg = make_config(tmp_path).llm  # the defaults (docs/llm.md)
    assert body["model"] == cfg.model == "claude-sonnet-5-5"
    assert body["max_tokens"] == cfg.max_tokens == 2048


def test_request_thinking_adaptive(tmp_path):
    h = Harness(tmp_path, [ok()], llm={"thinking": "adaptive"})
    h.plan()
    assert json.loads(h.requests[0].content)["thinking"] == {"type": "adaptive"}


def test_valid_tool_use_parsed(tmp_path):
    h = Harness(tmp_path, [ok()])
    r = h.plan()
    assert r.errors == [] and r.rejection_kind == "none" and not r.horizon_exceeded
    assert r.plan is not None and r.plan.steps[0].skill == "sit"
    assert r.tool_input == GOOD_INPUT
    assert {k: r.usage[k] for k in USAGE} == USAGE
    assert r.attempts == 1
    assert r.stop_reason == "tool_use"
    assert r.response_id == "msg_01" and r.request_id == "req_123"
    assert r.content == [{"type": "tool_use", "id": "toolu_1", "name": TOOL_NAME,
                          "input": GOOD_INPUT, **{k: v for k, v in r.content[0].items()
                                                  if k not in ("type", "id", "name", "input")}}]
    assert r.latency_ms >= 0 and r.total_ms >= r.latency_ms
    assert h.retries == [] and h.sleeps == []


def test_first_submit_plan_block_used(tmp_path):
    second = {"status": "DONE", "steps": [], "message": "x"}
    body = message([{"type": "text", "text": "thinking aloud"},
                    tool_use(GOOD_INPUT), tool_use(second, id_="toolu_2")])
    r = Harness(tmp_path, [ok(body)]).plan()
    assert r.plan.status == "PLAN" and len(r.content) == 3


def test_invalid_tool_input_validated(tmp_path):
    body = message([tool_use({"status": "PLAN", "steps": [{"skill": "sit"}] * (H + 1)})])
    r = Harness(tmp_path, [ok(body)]).plan()
    assert r.plan is None and r.horizon_exceeded and r.rejection_kind == "horizon"
    assert r.errors == [f"plan has {H + 1} steps; the maximum is {H}"]


def test_text_only_reply_is_no_tool_call(tmp_path):
    body = message([{"type": "text", "text": "I would sit down."}], stop_reason="end_turn")
    r = Harness(tmp_path, [ok(body)]).plan()
    assert r.plan is None and r.rejection_kind == "no_tool_call"
    assert r.errors == [ERR_NO_TOOL_CALL] and r.tool_input is None
    assert r.content[0]["type"] == "text"


def test_thinking_block_before_tool_use(tmp_path):
    body = message([{"type": "thinking", "thinking": "", "signature": "sig"},
                    tool_use(GOOD_INPUT)])
    r = Harness(tmp_path, [ok(body)]).plan()
    assert r.errors == [] and r.rejection_kind == "none"
    assert r.plan is not None and r.plan.steps[0].skill == "sit"
    assert [b["type"] for b in r.content] == ["thinking", "tool_use"]


def test_max_tokens(tmp_path):
    body = message([tool_use({"status": "PLAN"})], stop_reason="max_tokens")
    r = Harness(tmp_path, [ok(body)]).plan()
    assert r.plan is None and r.rejection_kind == "max_tokens"
    assert r.errors == [ERR_MAX_TOKENS] and r.stop_reason == "max_tokens"


def test_529_then_200(tmp_path):
    h = Harness(tmp_path, [httpx.Response(529, json={"type": "error"}), ok()])
    r = h.plan()
    assert r.attempts == 2 and r.plan is not None
    assert h.sleeps == [1.0]
    assert len(h.retries) == 1
    rec = h.retries[0]
    assert set(rec) == {"call_index", "attempt", "error_type", "status_code",
                        "attempt_latency_ms", "sleep_s"}
    assert rec["call_index"] == 7 and rec["attempt"] == 1
    assert rec["status_code"] == 529 and rec["sleep_s"] == 1.0


def test_429_retry_after(tmp_path):
    h = Harness(tmp_path, [httpx.Response(429, json={}, headers={"retry-after": "2"}), ok()])
    h.plan()
    assert h.sleeps == [2.0]


def test_retry_after_capped(tmp_path):
    h = Harness(tmp_path, [httpx.Response(429, json={}, headers={"retry-after": "120"}), ok()])
    h.plan()
    assert h.sleeps == [30.0]


def test_500_three_times_unavailable(tmp_path):
    h = Harness(tmp_path, [httpx.Response(500, json={})] * 3)
    with pytest.raises(LLMUnavailable) as ei:
        h.plan()
    assert len(h.requests) == 3 and h.sleeps == [1.0, 4.0] and len(h.retries) == 2
    assert ei.value.detail == "InternalServerError 500"


def test_400_unavailable_immediately(tmp_path):
    h = Harness(tmp_path, [httpx.Response(400, json={}), ok()])
    with pytest.raises(LLMUnavailable) as ei:
        h.plan()
    assert len(h.requests) == 1 and h.sleeps == [] and h.retries == []
    assert ei.value.detail == "BadRequestError 400"


def test_connection_error_retried(tmp_path):
    h = Harness(tmp_path, [httpx.ConnectError("boom"), ok()])
    r = h.plan()
    assert r.attempts == 2 and len(h.requests) == 2
    assert h.retries[0]["error_type"] == "APIConnectionError"
    assert h.retries[0]["status_code"] is None


def test_connection_errors_exhausted(tmp_path):
    h = Harness(tmp_path, [httpx.ConnectError("boom")] * 3)
    with pytest.raises(LLMUnavailable) as ei:
        h.plan()
    assert ei.value.detail == "APIConnectionError"


def test_wait_true_is_operator_interrupt(tmp_path):
    h = Harness(tmp_path, [httpx.Response(529, json={}), ok()], wait_result=True)
    with pytest.raises(LLMInterrupted) as ei:
        h.plan()
    assert ei.value.cause == "operator" and len(h.requests) == 1


def test_remaining_zero_no_request(tmp_path):
    h = Harness(tmp_path, [ok()])
    with pytest.raises(LLMInterrupted) as ei:
        h.plan(remaining=0.0)
    assert ei.value.cause == "task_time_limit" and h.requests == []


def test_stop_set_before_attempt(tmp_path):
    h = Harness(tmp_path, [ok()])
    h.stop_event.set()
    with pytest.raises(LLMInterrupted) as ei:
        h.plan()
    assert ei.value.cause == "operator" and h.requests == []


def test_backoff_longer_than_remaining(tmp_path):
    h = Harness(tmp_path, [httpx.Response(529, json={}), ok()])
    with pytest.raises(LLMInterrupted) as ei:
        h.plan(remaining=1.0)                 # sleep 1.0 >= remaining 1.0
    assert ei.value.cause == "task_time_limit" and h.retries == [] and h.sleeps == []


def test_zero_infra_retries(tmp_path):
    h = Harness(tmp_path, [httpx.Response(503, json={})], llm={"infra_max_retries": 0})
    with pytest.raises(LLMUnavailable):
        h.plan()
    assert len(h.requests) == 1
