# dispatcher/CLAUDE.md

The `dispatcher` package: the loop, the executor and its services (`execution/`), the plan contract (`planning/`: plan model, tool schema, validation, bounds, budget, verification), context and prompts, the LLM layer (`llm/`: planner protocol, retries, one module per provider, `PromptSurface`), the run log, config, the composition root (`app.py`, which builds and wires everything) and the CLI and Telegram transports. Repo-wide rules are in the root [CLAUDE.md](../CLAUDE.md).

## Commands

```bash
uv run pytest dispatcher -q                    # dispatcher unit tests only
uv run pytest -m integration                   # after touching the executor, subprocess handling or the loop
uv run pytest --update-golden                  # after changing fixed texts, rendering or the catalog; then git diff dispatcher/tests/golden
uv run go2 catalog                             # check system text, catalog, tool schema and registry hash after changing prompts or the registry
```

## Docs

| Doc | Covers |
|---|---|
| [loop-and-context.md](docs/loop-and-context.md) | `submit_plan` schema, validation, return reasons, budgets, outcome codes and operator messages, context layout, fixed texts |
| [execution.md](docs/execution.md) | The executor's parts in `execution/` (env, process, kill switch, control link, stop path, outcome), the order of a step, `ExecResult` and `ProcessRun` |
| [llm.md](docs/llm.md) | Providers (Anthropic, OpenRouter), model choice, request parameters, schema retry, horizon rejection, infrastructure retries |
| [run-log.md](docs/run-log.md) | Run log files, record types, events and `emit`, observers, `index.jsonl`, study metrics |
| [report.md](../analysis/docs/report.md) | The run-log report (`go2 report`, `analysis` package; `cli.py` only calls it) |
| [configuration.md](../docs/configuration.md) | Config keys, validation rules, exit codes, CLI overrides, `.env` |
| [running.md](../docs/running.md) | `go2` CLI, Telegram bot, stub vs real, single-instance lock |
| [testing.md](../tests/docs/testing.md) | Test layout, helpers and fakes, markers, golden files |
| [contract.md](../contract/docs/contract.md) | `SKILL.md` format, invocation, response schema, error codes and policies the dispatcher consumes |
| [skills.md](../skills/docs/skills.md) | Catalog generation, registry hash, the skills |
| [service.md](../services/robot/docs/service.md) | Robot service and its lease, stub backend, fault injection |
| [vision.md](../services/vision/docs/vision.md) | Vision service, stub camera, object detection |

## Gotchas

- Fixed texts in `prompts.py` (model) and `operator_messages.py` (operator outcomes, read again as Previous task) change the golden files and make runs incomparable; `transports/texts.py` (CLI and Telegram) changes only the golden files; only the system text (`SYSTEM_TEMPLATE`) also changes the registry hash ([skills.md](../skills/docs/skills.md#catalog-and-registry-hash)). Regenerate with `--update-golden` and review the diff.
- Changes to `context.py` (step rendering, user message) change the `context_*.txt` golden files; a `SKILL.md` change on the skills side changes `catalog.txt`.
- `anthropic` and `httpx` are imported lazily inside `llm/anthropic_planner.py` and `llm/openrouter_planner.py`; a top-level `anthropic` import breaks the "CLI does not import `anthropic`" test ([llm.md](docs/llm.md#design-decisions)).
- The dispatcher imports `contract`, `skills` and the client surfaces of `services.robot` and `services.vision` (never `services.robot.service`, `services.robot.backends`, `services.vision.service`, `services.vision.detectors` or `services.backend`; it reaches the stub world only through `services.robot.stub`, enforced by an import contract), never the reverse. `dispatcher/tests/` holds unit tests only; tests that spawn subprocesses or need both packages go in root `tests/` ([testing.md](../tests/docs/testing.md#layout)).
- No `temperature`, no forced `tool_choice` (Anthropic; OpenRouter forces it), no prompt caching: deliberate, not omissions ([llm.md](docs/llm.md#design-decisions)).
- Nothing raw (stderr, tracebacks, provider error text) may enter the model context ([loop-and-context.md](docs/loop-and-context.md#design-decisions)).
- A new record type follows the recipe in [run-log.md](docs/run-log.md#adding-a-record-type); `RunLog.emit` ends in `assert_never`, so basedpyright fails until its `case` exists. Observers match only what they show (`case _: pass`).
- A new or changed config key updates [configuration.md](../docs/configuration.md) in the same change.
