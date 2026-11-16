# analysis/CLAUDE.md

The `analysis` package: the run-log report behind `go2 report [FOLDER]` (`logs.py` reads the index and task files, `report.py` builds and renders the report). It only reads logs. Repo-wide rules are in the root [CLAUDE.md](../CLAUDE.md).

## Commands

```bash
uv run pytest -q analysis                      # analysis unit tests only
uv run pytest -q dispatcher/tests/unit/test_report.py   # the go2 report CLI cases
uv run pytest -q tests/test_report_skill_sets.py        # the default skill lists against the registry
uv run go2 report [FOLDER]                     # the report on existing run logs; --since / --until / --model; no key needed
uv run lint-imports                            # import contracts; must pass before a commit
```

## Docs

| Doc | Covers |
|---|---|
| [report.md](docs/report.md) | What the report selects, computes and prints, its arguments, the default motion and stationary skill lists, skip reasons |
| [run-log.md](../dispatcher/docs/run-log.md) | The log format the report reads (records, `index.jsonl`) and the study metric definitions |
| [running.md](../docs/running.md) | The `go2` CLI, exit codes |
| [testing.md](../tests/docs/testing.md) | Test layout, import boundaries |

## Gotchas

- `analysis` imports from the project only `dispatcher.runlog.format` (file-format constants, `TOKEN_KEYS`) and `dispatcher.config` (`Provider`); only `dispatcher.transports.cli` and root `tests` import it. Both are import contracts, and they cover `analysis/tests/` too, so a test that runs `go2 report` through the CLI lives with the CLI's tests ([report.md](docs/report.md#the-package)).
- A new log field the report needs is a run-log change owned by `dispatcher/docs/run-log.md`, made there first; old logs lack it and are skipped as `older log, fields missing` ([report.md](docs/report.md#what-it-computes)).
- `MOTION_SKILLS` and `STATIONARY_SKILLS` must classify every built-in skill; a test checks them against each `SKILL.md`'s `moves` ([report.md](docs/report.md#skill-lists)).
