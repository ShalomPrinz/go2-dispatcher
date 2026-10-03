# tests/CLAUDE.md

The test infrastructure: the pytest plugin, opt-in flags and markers, shared helpers, the golden-file mechanism, coverage, pytest config and CI, plus the cross-service tests in `tests/integration/`. Repo-wide rules are in the root [CLAUDE.md](../CLAUDE.md).

## Commands

```bash
uv run pytest -q                               # default suite; must pass on any machine after uv sync
uv run pytest -q dispatcher/tests skills/tests # unit tests of both packages only
uv run pytest -q tests                         # cross-service tests only
uv run pytest -m integration                   # real subprocesses on the stub backend
uv run pytest -q --durations=10                # slowest tests; report wall time before and after a speed change
uv run lint-imports                            # import contracts (skills boundary); must pass before a commit
uv run pytest --cov                            # branch coverage incl. subprocesses; opt-in, about 60 % slower
uv run pytest --update-golden                  # only after a behaviour change; then git diff dispatcher/tests/golden
uv run pytest --run-live -s tests/integration/test_live_llm.py  # real API, needs ANTHROPIC_API_KEY; paid; only when a brief asks, never tests-dev
uv run pytest --run-robot                      # real Go2; supervised session only, never from an agent
```

## Docs

| Doc | Covers |
|---|---|
| [testing.md](docs/testing.md) | [Running](docs/testing.md#running-the-tests), [lint](docs/testing.md#lint-and-format), [import boundaries](docs/testing.md#import-boundaries), [coverage](docs/testing.md#coverage), [layout](docs/testing.md#layout), [markers and flags](docs/testing.md#markers-and-flags), [live LLM test](docs/testing.md#live-llm-test), [helpers](docs/testing.md#helpers), [golden files](docs/testing.md#golden-files), [writing tests](docs/testing.md#writing-tests), [design decisions](docs/testing.md#design-decisions) |
| [skills.md](../skills/docs/skills.md) | Stub backend and fault kinds the tests drive, catalog and registry hash |
| [loop-and-context.md](../dispatcher/docs/loop-and-context.md) | Fixed texts and context layout behind the golden files |
| [robot.md](../skills/docs/robot.md#supervised-robot-checklist) | Supervised robot checklist (the robot checks are manual in v1) |

## Gotchas

- The default suite stays offline and fast: no network, API key, SDK or robot. Anything else is marked `live_llm` or `robot` ([testing.md](docs/testing.md#writing-tests)).
- Every test has a 30 s timeout (`pytest-timeout`); a hang shows as a timeout, not a stuck run ([testing.md](docs/testing.md#coverage)).
- Options live in `tests/pytest_plugin.py`, loaded by `addopts`, not a root `conftest.py`, so they work for any subset ([testing.md](docs/testing.md#design-decisions)).
- Package unit tests live in `dispatcher/tests/` and `skills/tests/`; anything spawning subprocesses or needing both packages goes in root `tests/`. `skills/tests/` never imports `dispatcher` ([testing.md](docs/testing.md#layout)).
- Golden files change only with a behaviour change (fixed texts, renderer, `SKILL.md`), never to make a test pass. Regenerate, review the diff; it also changes the registry hash ([testing.md](docs/testing.md#golden-files)).
- CI runs `lint-imports` and the default suite only; lint and format stay out of CI by decision ([testing.md](docs/testing.md#design-decisions)).
- A change to how tests run (flags, markers, helpers, layout, CI) updates [testing.md](docs/testing.md) in the same change.
