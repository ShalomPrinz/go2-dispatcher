# The LLM layer

How the dispatcher calls the planner model: provider and model, the exact request, how a reply is turned into a plan or rejected, schema retry, horizon rejection, and infrastructure retries. What the model must return and what it sees are in [loop-and-context.md](loop-and-context.md). Code: `dispatcher/llm.py`; config keys under `[llm]` in [configuration.md](../../docs/configuration.md).

## Provider and model

- **Provider:** `llm.provider` selects one of two (default `anthropic`; the provider is logged in `task_start.config` and every `index.jsonl` row):
  - `anthropic`: the Anthropic Messages API, through the official `anthropic` Python SDK (1.x, which runs on `httpx2`). This is the study's planner; research data comes from it only ([run-log.md](run-log.md#files)).
  - `openrouter`: OpenRouter's OpenAI-compatible chat completions endpoint (`https://openrouter.ai/api/v1/chat/completions`), called with `httpx` directly. It exists to develop and test with free models; its runs are kept apart from the research data.
- **Model:** `llm.model`, default `claude-sonnet-5-5` (Claude Sonnet 5.5). The default is a starting value; the model id is logged in `task_start.config` and in every `index.jsonl` row. With `openrouter`, `llm.model` is an OpenRouter model id and must be set explicitly (a config error otherwise).
- **Thinking:** minimal, via `llm.thinking = "between_tools"`: no extended thinking, but not strictly zero thinking output (below). "Thinking off" in these docs means this setting. `llm.thinking` applies to `anthropic` only: with `openrouter` it is ignored (not sent, and `null` in `index.jsonl`).

An alternative LLM layer (Jev, a "TypeSafe AI" decision model) is under consideration, not decided ([roadmap.md](../../docs/roadmap.md#open-questions)).

## The request

`AnthropicPlanner` is built with the process's `PromptSurface` (system blocks and tool definition); `plan()` takes only the per-call user message, call index, deadline, stop event and retry callback, and sends exactly this (one request per attempt):

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

### OpenRouter request

`OpenRouterPlanner` takes the same surface and arguments and sends the same content in the OpenAI-compatible shape, one `POST` per attempt with `Authorization: Bearer $OPENROUTER_API_KEY` and timeout `min(llm.request_timeout_s, remaining_task_s)`:

```json
{
  "model": "<llm.model>",
  "max_tokens": 2048,
  "messages": [
    {"role": "system", "content": [{"type": "text", "text": "<system_text>"}, {"type": "text", "text": "## Skills\n<catalog>"}]},
    {"role": "user", "content": "<user_message>"}
  ],
  "tools": [{"type": "function", "function": {"name": "submit_plan", "description": "...", "parameters": {"...": "submit_plan input_schema"}}}],
  "tool_choice": {"type": "function", "function": {"name": "submit_plan"}}
}
```

The two system blocks stay two text parts of one system message. The tool is the logged `tool_schema` wrapped as a function tool (`input_schema` becomes `parameters`); `task_start.tool_schema` holds the unwrapped definition. `tool_choice` forces the `submit_plan` call, unlike the Anthropic request (Design decisions). Nothing else is sent: no `thinking` or reasoning setting, no sampling parameters, no provider routing preferences, no streaming. Whether a given free model supports tool calls (or honours the forced choice) is the model's affair: one that ignores the tool gives `no_tool_call` rejections, handled as for Anthropic.

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

**Unverified.** No request has yet been sent to the live API with these parameters. In particular it has not been confirmed that Sonnet 5.5 accepts `between_tools` together with auto `tool_choice` and a tool definition. The parameter facts above come from Anthropic's documentation ([references.md](../../docs/references.md#llm)). The OpenRouter request is equally unverified on the live API. To verify, run the live test with an API key ([testing.md](../../tests/docs/testing.md)); it is tracked in [roadmap.md](../../docs/roadmap.md#pending-human-work).

## Response handling

`AnthropicPlanner` returns the reply unvalidated: the input of the first `tool_use` block named `submit_plan` (`thinking` and `text` blocks before it are skipped, further calls are ignored), or none. The loop then checks it with `llm.check_reply`, in this order ([loop-and-context.md](loop-and-context.md#design-decisions)):

1. `stop_reason == "max_tokens"` → invalid, `rejection_kind = "max_tokens"`, error `reply was cut off; keep the plan shorter`. The tool input, if any, is logged but not validated.
2. No `submit_plan` block (for example a text-only reply) → invalid, `rejection_kind = "no_tool_call"`, error `no submit_plan call in reply`.
3. The block's input is validated ([loop-and-context.md](loop-and-context.md#plan-validation)); `rejection_kind` is `horizon`, `schema`, `semantic` or `none`.

All content blocks, `usage` (verbatim), `stop_reason`, latency, attempts, `response_id` and `request_id` are logged in the `llm_response` record ([run-log.md](run-log.md)). A reply with `stop_reason = "refusal"` (Sonnet 5.5 safety classifiers) has no `submit_plan` call, so it is handled as `no_tool_call`; there is no special handling and no fallback model.

**OpenRouter replies** go through the same checks. `OpenRouterPlanner` takes the first tool call named `submit_plan` in the first choice's `message.tool_calls` and parses its `arguments` JSON string; text that is not valid JSON is kept as the raw string and fails schema validation. `stop_reason` is the first choice's `finish_reason`, verbatim: `length` is treated like `max_tokens` (step 1, `CUT_OFF_STOP_REASONS` in `llm.py`), and a reply without the tool call (`stop`, `content_filter`, `error` or none) is `no_tool_call`. `usage` is OpenRouter's usage object and `content` the full `choices` list, both verbatim (no conversion to the Anthropic shape); `response_id` is the generation `id` and `request_id` is null.

## Schema retry

An invalid reply (any non-`none` rejection kind) gets exactly **one** retry:

- The same system blocks and tool. The user message is rebuilt from the current task state, plus the Rejection section with one line per error ([loop-and-context.md](loop-and-context.md#user-message)). Nothing else changed since the original request, so it differs from it only in the Budget's model-call count, which includes the rejected call.
- The retry's return reason is `schema_retry`; the `llm_request` record's `retry_of` holds the reason being retried.
- The retry is an LLM call and counts toward `loop.max_llm_calls` (checked before it is sent).
- If the retry is also invalid, the task ends `LLM_INVALID`.
- Invalid replies never count as failures. Each invalid reply is logged as `plan_invalid` with its `rejection_kind`.

## Horizon rejection

A plan with more steps than `loop.planning_horizon` is **rejected as a whole and retried, never truncated**. The horizon is stated three times: in the system text, as `maxItems` in the tool schema, and by validation.

Every such reply is logged as `plan_invalid` with `rejection_kind = "horizon"` (with the raw tool input, `steps_in_plan` and `horizon`), and `task_end.horizon_rejections` counts them, so the rate can be measured per condition.

**Monitoring rule.** Watch the horizon-rejection rate in the run logs (`plan_invalid` records with `rejection_kind == "horizon"`). Even 1 in 100 calls is a lot. If the rate is high, inspect those plans and either switch to truncation or fix it another way, for example by stating the horizon more prominently. This is an open question until there is data ([roadmap.md](../../docs/roadmap.md#open-questions)).

## Infrastructure retries

The SDK's own retries are disabled (`max_retries=0`); `AnthropicPlanner` retries itself so that every retry is logged and bounded by the task's deadline.

- **Retryable:** connection errors (including timeouts) and HTTP 408, 409, 429 and every status ≥ 500 (including 529 overloaded). Anything else (for example a 400) is not retried.
- **Attempts:** up to `len(llm.infra_backoff_s)` (2) retries. Before retry *i*, sleep `llm.infra_backoff_s[i]` (`[1.0, 4.0]`). A numeric `retry-after` header can raise the sleep, capped at 30 s.
- **Per-attempt timeout:** `min(llm.request_timeout_s, remaining task time)`.
- **Stop and deadline:** before each attempt, a passed deadline raises an interrupt (task ends `TIME_LIMIT_EXCEEDED`) and a stop request raises one (task ends `STOPPED`). If the sleep would reach the deadline, the task ends `TIME_LIMIT_EXCEEDED` at once. The backoff sleep wakes on a stop request. A request already in flight is not cancelled; the stop takes effect when it returns.
- **Exhausted or non-retryable:** the task ends `LLM_ERROR` with detail `{ErrorClass} {status}`.
- **OpenRouter:** the same rules and the same loop (`_call_with_retries` in `llm.py`), classified on `httpx` errors: any `httpx.TransportError` (connection error or timeout; `status_code` null) and an `httpx.HTTPStatusError` with a retryable status are retried. A body with an `error` object and no `choices` (OpenRouter can return one with HTTP 200) is an `OpenRouterError` whose status is the error's numeric `code`, retried by the same status rule; an unparseable body is an `OpenRouterError` without a status and is not retried. `retry-after` is honoured as above. Error classes in `llm_retry` and the `LLM_ERROR` detail are these (for example `HTTPStatusError 400`, `ReadTimeout`).
- Each retry writes an `llm_retry` record (call index, attempt, error type, status code, attempt latency, sleep). Retries are neither LLM calls nor failures; `llm_response.attempts` and `total_ms` include them, `latency_ms` covers the successful attempt only.

## Design decisions

- **Claude Sonnet 5.5 as the planner, with the request shaped to what it accepts.** When an earlier design (forced `tool_choice`, a fixed `temperature`) turned out to be rejected by Sonnet 5.5, the request was changed rather than the model. Rejected: switching to an older model that still accepts forced tool choice and `temperature` (such as Opus 4.6).
- **Thinking at its lowest setting (`between_tools`), called "thinking off".** Thinking adds variable latency and output tokens, and those are measured variables. Sonnet 5.5 does not allow `disabled`, so the lowest accepted setting is used and kept constant across conditions; `adaptive` exists only as a deliberate condition.
- **Auto `tool_choice` plus one schema retry.** Forced tool choice is unavailable on Sonnet 5.5; a missing call is treated like any other invalid reply, so it is measured rather than hidden.
- **Forced `tool_choice` for OpenRouter only.** Free models often answer in plain text under `auto`, which costs a `no_tool_call` schema retry and free-tier quota; forcing `submit_plan` avoids that. The Anthropic request keeps `auto` (Sonnet 5.5 rejects a forced choice), so the research request and golden files are unchanged. The `no_tool_call` check stays, because a model or provider may ignore the forced choice. Rejected: `auto` for OpenRouter too, which mirrored Anthropic but wasted retries on free models.
- **No `temperature`.** Not possible on Sonnet 5.5. The cost is non-deterministic planning; the study must account for run-to-run variation.
- **Non-strict tool mode.** Strict mode cannot enforce `maxItems` or numeric ranges, and it injects an extra system prompt that distorts token counts. The dispatcher's own validation is the enforcer.
- **`max_tokens` 2048** as headroom against cut-off replies; it does not affect cost or measurements unless a reply would be cut off.
- **Reject, never truncate, over-long plans**, with the rate logged and a rule for revisiting the choice (above). Truncation would run a plan the model never made and corrupt the data.
- **Exactly one schema retry**, then `LLM_INVALID`: bounded cost, and a second invalid reply is itself a measurable outcome. Invalid replies are LLM calls but never failures.
- **The schema retry's user message is rebuilt, not copied.** It goes through the same context builder as every request, so the Budget's model-call count includes the rejected call and stays true to the count the call budget checks. Rejected: resending the original message byte for byte, which showed the model one call fewer than it had used.
- **Own infrastructure retries, logged separately, never counted as LLM calls or replans.** Transport problems must not distort the planning metrics. This replaces OpenClaw's provider retry; model failover is not covered, which is accepted ([architecture.md](../../docs/architecture.md#why-a-purpose-built-dispatcher)).
- **`anthropic` and `httpx` are imported lazily**, inside the planners and their retry classifiers. Importing it costs about 0.8 s, which every `go2 catalog`, `state` and `--reset-stub` and every stub run with the test planner would otherwise pay without calling the API. A fresh-interpreter test checks that the CLI transport does not import it.
- **The planner is built with the prompt surface; `plan()` takes only per-call arguments.** The system blocks and tool definition are fixed per process, so `make_planner(cfg, surface)` passes the one `PromptSurface` to `AnthropicPlanner` (and a test planner factory) at construction, and `PlannerClient` exposes it as `surface`. The dispatcher takes no surface of its own: it builds its run-log session from `planner.surface`, so what is logged is what the planner sends ([run-log.md](run-log.md#design-decisions)). Rejected: passing `system` and `tool_schema` on every `plan()` call, which repeated constants in each call and let a caller send something other than the logged surface.
- **OpenRouter as a second provider, through `httpx` directly.** It lets development and testing run on free models without paying for the Anthropic API. The endpoint is OpenAI-compatible and the request is small, so a hand-written `httpx` call (already a dependency) is enough and keeps the request exact and visible. Rejected: the `openai` SDK, a new dependency for one POST whose own retries would have to be disabled like Anthropic's.
- **Provider chosen by config (`llm.provider`), and logs separated by provider.** The provider is a condition of the run, so it lives with the other conditions in the config, is logged in `task_start.config` and `index.jsonl`, and selects the log folder ([run-log.md](run-log.md#design-decisions)). Rejected: a CLI-only switch, which would not be recorded with the config. `llm.thinking` is ignored for `openrouter` rather than rejected, because it has a default and rejecting would force every OpenRouter config to remove a key it never set; `llm.model` must be set explicitly because the Anthropic default is not an OpenRouter id.
- **OpenRouter replies are logged in their native shape.** `usage` and `content` (the `choices` list) are stored as OpenRouter returns them; the only mapping is `length` → the `max_tokens` check, and the index token columns, which read `prompt_tokens` / `completion_tokens` ([run-log.md](run-log.md#indexjsonl)). Converting to the Anthropic shape would lose fields and invent others; OpenRouter runs are never pooled with the research data, so a shared shape buys nothing.
- **One retry loop for both providers.** Each planner only maps its errors to (error class, status, retryable, `retry-after`); deadline, stop, backoff and `llm_retry` logging are shared, so the two cannot drift.
- **No prompt caching.** It would confound token comparisons between conditions ([loop-and-context.md](loop-and-context.md#design-decisions)).
