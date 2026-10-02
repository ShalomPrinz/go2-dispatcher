# The LLM layer

How the dispatcher calls the planner model: provider and model, the exact request, how a reply is turned into a plan or rejected, schema retry, horizon rejection, and infrastructure retries. What the model must return and what it sees are in [loop-and-context.md](loop-and-context.md). Code: `dispatcher/llm.py`; config keys under `[llm]` in [configuration.md](../../docs/configuration.md).

## Provider and model

- **Provider:** the Anthropic Messages API, through the official `anthropic` Python SDK (1.x, which runs on `httpx2`).
- **Model:** `llm.model`, default `claude-sonnet-5-5` (Claude Sonnet 5.5). The default is a starting value; the model id is logged in `task_start.config` and in every `index.jsonl` row.
- **Thinking:** minimal, via `llm.thinking = "between_tools"`: no extended thinking, but not strictly zero thinking output (below). "Thinking off" in these docs means this setting.

An alternative LLM layer (Jev, a "TypeSafe AI" decision model) is under consideration, not decided ([roadmap.md](../../docs/roadmap.md#open-questions)).

## The request

`AnthropicPlanner.plan()` sends exactly this (one request per attempt):

```python
client = anthropic.Anthropic(api_key=api_key, max_retries=0)  # own retries, below
client.with_options(timeout=min(llm.request_timeout_s, remaining_task_s)).messages.create(
    model=llm.model,  # "claude-sonnet-5-5"
    max_tokens=llm.max_tokens,  # 2048
    system=[{"type": "text", "text": system_text}, {"type": "text", "text": "## Skills\n" + catalog}],
    tools=[submit_plan_definition],  # the only tool; no "strict"
    tool_choice={"type": "auto"},
    thinking={"type": llm.thinking},  # "between_tools"
    messages=[{"role": "user", "content": user_message}],
)
```

Nothing else is sent: no `temperature` or other sampling parameter, no `output_config` (so effort is the model's default), no `cache_control`, no streaming, no fallback models, no beta headers. The system blocks, tool definition and user message are described in [loop-and-context.md](loop-and-context.md#context-layout).

### Sonnet 5.5 parameter constraints

Sonnet 5.5 returns HTTP 400 for three things an earlier design relied on. A 400 is not retried, so each would end every task `LLM_ERROR`. The request is shaped around them:

| Sonnet 5.5 rejects | What we send instead | Consequence |
|---|---|---|
| Forced `tool_choice` (`{"type": "tool"}` / `"any"`) | `tool_choice={"type": "auto"}`; `submit_plan` is the only tool and the system text asks for exactly one call | The model is asked, not forced, to call the tool. A reply without the call is invalid and gets the one schema retry, then `LLM_INVALID`. |
| A non-default `temperature` (or other sampling parameter) | Nothing; there is no `temperature` config key (adding one is an unknown-key error) | Planning is **not deterministic**. Repeated runs of the same task can differ. |
| `thinking: {"type": "disabled"}` | `thinking={"type": "between_tools"}` | See below. |

**Thinking.** On Sonnet 5.5, `between_tools` is the lowest thinking setting: the model does no extended thinking; short progress notes it writes between tool calls may still come back as `thinking` blocks. Thinking cannot be switched off completely on Sonnet 5.5 (`disabled` is rejected), so "thinking off" means this lowest setting, not zero thinking output. Any such blocks are logged in `llm_response.content`; that their tokens are counted in `usage` is *unverified on the live API*. It is accepted only at effort `high` or below; we send no effort, so the model default (`high`) applies. It takes no other field inside `thinking`. The other allowed value, `llm.thinking = "adaptive"`, lets the model think and is meant only as a deliberate experimental condition; `thinking` is logged in every `index.jsonl` row. `between_tools` is accepted by Sonnet 5.5 only: to run another model, set `llm.thinking = "adaptive"` (which means thinking on) or change the code.

**`max_tokens` 2048** is headroom against cut-off replies. It is only a ceiling: cost follows the tokens actually generated, and it affects measurements only if a reply would be cut off. A 5-step plan at `between_tools` needs far less. A reply cut off at the limit is invalid (below).

### Live-API verification status

**Unverified.** No request has yet been sent to the live API with these parameters. In particular it has not been confirmed that Sonnet 5.5 accepts `between_tools` together with auto `tool_choice` and a tool definition. The parameter facts above come from Anthropic's documentation ([references.md](../../docs/references.md#llm)). To verify, run the live test with an API key ([testing.md](../../tests/docs/testing.md)); it is tracked in [roadmap.md](../../docs/roadmap.md#pending-human-work).

## Response handling

For each response, in this order:

1. `stop_reason == "max_tokens"` → invalid, `rejection_kind = "max_tokens"`, error `reply was cut off; keep the plan shorter`. The tool input, if any, is logged but not validated.
2. The first `tool_use` block named `submit_plan` is taken; `thinking` and `text` blocks before it are skipped, and further calls are ignored. No such block (for example a text-only reply) → invalid, `rejection_kind = "no_tool_call"`, error `no submit_plan call in reply`.
3. The block's input is validated ([loop-and-context.md](loop-and-context.md#plan-validation)); `rejection_kind` is `horizon`, `schema`, `semantic` or `none`.

All content blocks, `usage` (verbatim), `stop_reason`, latency, attempts, `response_id` and `request_id` are logged in the `llm_response` record ([run-log.md](run-log.md)). A reply with `stop_reason = "refusal"` (Sonnet 5.5 safety classifiers) has no `submit_plan` call, so it is handled as `no_tool_call`; there is no special handling and no fallback model.

## Schema retry

An invalid reply (any non-`none` rejection kind) gets exactly **one** retry:

- The same system blocks and tool. The user message is the original one, byte for byte, plus the Rejection section with one line per error ([loop-and-context.md](loop-and-context.md#user-message)).
- The retry's return reason is `schema_retry`; the `llm_request` record's `retry_of` holds the reason being retried.
- The retry is an LLM call and counts toward `loop.max_llm_calls` (checked before it is sent).
- If the retry is also invalid, the task ends `LLM_INVALID`.
- Invalid replies never count as failures. Each invalid reply is logged as `plan_invalid`, or as `horizon_rejection` when the horizon was exceeded.

## Horizon rejection

A plan with more steps than `loop.planning_horizon` is **rejected as a whole and retried, never truncated**. The horizon is stated three times: in the system text, as `maxItems` in the tool schema, and by validation.

Every such reply is logged as its own `horizon_rejection` record (with the raw tool input, the number of steps and the horizon), and `task_end.horizon_rejections` counts them, so the rate can be measured per condition.

**Monitoring rule.** Watch the horizon-rejection rate in the run logs. Even 1 in 100 calls is a lot. If the rate is high, inspect those plans and either switch to truncation or fix it another way, for example by stating the horizon more prominently. This is an open question until there is data ([roadmap.md](../../docs/roadmap.md#open-questions)).

## Infrastructure retries

The SDK's own retries are disabled (`max_retries=0`); `AnthropicPlanner` retries itself so that every retry is logged and bounded by the task's deadline.

- **Retryable:** connection errors (including timeouts) and HTTP 408, 409, 429 and every status ≥ 500 (including 529 overloaded). Anything else (for example a 400) is not retried.
- **Attempts:** up to `len(llm.infra_backoff_s)` (2) retries. Before retry *i*, sleep `llm.infra_backoff_s[i]` (`[1.0, 4.0]`). A numeric `retry-after` header can raise the sleep, capped at 30 s.
- **Per-attempt timeout:** `min(llm.request_timeout_s, remaining task time)`.
- **Stop and deadline:** before each attempt, a passed deadline raises an interrupt (task ends `TIME_LIMIT_EXCEEDED`) and a stop request raises one (task ends `STOPPED`). If the sleep would reach the deadline, the task ends `TIME_LIMIT_EXCEEDED` at once. The backoff sleep wakes on a stop request. A request already in flight is not cancelled; the stop takes effect when it returns.
- **Exhausted or non-retryable:** the task ends `LLM_ERROR` with detail `{ErrorClass} {status}`.
- Each retry writes an `llm_retry` record (call index, attempt, error type, status code, attempt latency, sleep). Retries are neither LLM calls nor failures; `llm_response.attempts` and `total_ms` include them, `latency_ms` covers the successful attempt only.

## Design decisions

- **Claude Sonnet 5.5 as the planner, with the request shaped to what it accepts.** When an earlier design (forced `tool_choice`, a fixed `temperature`) turned out to be rejected by Sonnet 5.5, the request was changed rather than the model. Rejected: switching to an older model that still accepts forced tool choice and `temperature` (such as Opus 4.6).
- **Thinking at its lowest setting (`between_tools`), called "thinking off".** Thinking adds variable latency and output tokens, and those are measured variables. Sonnet 5.5 does not allow `disabled`, so the lowest accepted setting is used and kept constant across conditions; `adaptive` exists only as a deliberate condition.
- **Auto `tool_choice` plus one schema retry.** Forced tool choice is unavailable; a missing call is treated like any other invalid reply, so it is measured rather than hidden.
- **No `temperature`.** Not possible on Sonnet 5.5. The cost is non-deterministic planning; the study must account for run-to-run variation.
- **Non-strict tool mode.** Strict mode cannot enforce `maxItems` or numeric ranges, and it injects an extra system prompt that distorts token counts. The dispatcher's own validation is the enforcer.
- **`max_tokens` 2048** as headroom against cut-off replies; it does not affect cost or measurements unless a reply would be cut off.
- **Reject, never truncate, over-long plans**, with the rate logged and a rule for revisiting the choice (above). Truncation would run a plan the model never made and corrupt the data.
- **Exactly one schema retry**, then `LLM_INVALID`: bounded cost, and a second invalid reply is itself a measurable outcome. Invalid replies are LLM calls but never failures.
- **Own infrastructure retries, logged separately, never counted as LLM calls or replans.** Transport problems must not distort the planning metrics. This replaces OpenClaw's provider retry; model failover is not covered, which is accepted ([architecture.md](../../docs/architecture.md#why-a-purpose-built-dispatcher)).
- **`anthropic` is imported lazily**, inside `AnthropicPlanner` and its retry helper. Importing it costs about 0.8 s, which every `go2 catalog`, `state` and `--reset-stub` and every stub run with the test planner would otherwise pay without calling the API. A fresh-interpreter test checks that the CLI transport does not import it.
- **No prompt caching.** It would confound token comparisons between conditions ([loop-and-context.md](loop-and-context.md#design-decisions)).
