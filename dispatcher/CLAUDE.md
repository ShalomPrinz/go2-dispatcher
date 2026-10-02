# dispatcher/CLAUDE.md

The `dispatcher` package: the loop, plan validation, budgets, context and prompts, the LLM layer, the run log, config and the CLI and Telegram transports. Repo-wide rules are in the root [CLAUDE.md](../CLAUDE.md).

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
| [llm.md](docs/llm.md) | Model choice, request parameters, schema retry, horizon rejection, infrastructure retries |
| [run-log.md](docs/run-log.md) | Run log files, record types, `index.jsonl`, study metrics |
| [configuration.md](../docs/configuration.md) | Config keys, validation rules, exit codes, CLI overrides, `.env` |
| [running.md](../docs/running.md) | `go2` CLI, Telegram bot, stub vs real, single-instance lock |
| [testing.md](../tests/docs/testing.md) | Test layout, helpers and fakes, markers, golden files |
| [skills.md](../skills/docs/skills.md) | Skill contract and response schema the executor consumes, registry hash |

## Gotchas

- Fixed texts in `prompts.py` change the registry hash and the golden files and make runs incomparable. Regenerate with `--update-golden` and review the diff.
- Changes to `render.py` or `context.py` change the `context_*.txt` golden files; a `SKILL.md` change on the skills side changes `catalog.txt`.
- `anthropic` is imported lazily inside `llm.py`; a top-level import breaks the "CLI does not import `anthropic`" test ([llm.md](docs/llm.md#design-decisions)).
- The dispatcher imports `skills`, never the reverse. `dispatcher/tests/` holds unit tests only; tests that spawn subprocesses or need both packages go in root `tests/` ([testing.md](../tests/docs/testing.md#layout)).
- No `temperature`, no forced `tool_choice`, no prompt caching: deliberate, not omissions ([llm.md](docs/llm.md#design-decisions)).
- Nothing raw (stderr, tracebacks, provider error text) may enter the model context ([loop-and-context.md](docs/loop-and-context.md#design-decisions)).
- A new or changed config key updates [configuration.md](../docs/configuration.md) in the same change.
