# skills/CLAUDE.md

The `skills` package: the skill contract and response schema, the `SKILL.md` catalog, the skills themselves, the stub backend and fault injection, the real Go2 backend, state sampling and posture. Repo-wide rules are in the root [CLAUDE.md](../CLAUDE.md).

## Commands

```bash
uv run pytest skills -q                        # skills unit tests only
uv run pytest -m integration                   # after touching a skill's subprocess behaviour, the stub or the response format
uv run pytest --update-golden                  # after a SKILL.md change; then git diff dispatcher/tests/golden
uv run go2 catalog                             # after touching the catalog: check catalog and registry hash
uv run go2 --reset-stub                        # stub checks: back to standing (sit persists across tasks)
uv run go2 --fault 1:hang run "…"              # stub fault injection: error, hang, crash, garbage (needs ANTHROPIC_API_KEY)
```

## Docs

| Doc | Covers |
|---|---|
| [skills.md](docs/skills.md) | `SKILL.md` frontmatter, catalog and registry hash, invocation, response schema, error codes, timeout and motion-cost policies, stub and fault injection, adding a skill, running one by hand |
| [robot.md](docs/robot.md) | Robot facts, SDK and DDS usage, state sampling, posture rule, settle waits, real backend, supervised robot checklist |
| [safety.md](../docs/safety.md) | Stop path, motion budget, bounds, supervised-operation rules |
| [configuration.md](../docs/configuration.md) | `robot.*` and `stub.*` config keys, `.env` |
| [setup.md](../docs/setup.md) | Installing the `robot` and `vision` extras, network interface |
| [testing.md](../tests/docs/testing.md) | Test layout, markers, fault injection in tests, robot checks |

## Gotchas

- The docs define the skill contract; code follows [skills.md](docs/skills.md), not the other way round.
- `SKILL.md` frontmatter is a fixed text: any wording change changes the catalog, the registry hash and `catalog.txt`, and makes runs incomparable. Regenerate with `--update-golden` and review the diff ([skills.md](docs/skills.md#catalog-and-registry-hash)).
- Real-backend code (`real.py`) cannot be run from an agent: no `--backend real`, no `robot.backend = "real"`, no `--run-robot`. Verify it with unit tests and the stub only ([safety.md](../docs/safety.md#supervised-operation-rules)).
- Mark robot facts not yet checked on the robot as *unverified*. Checklist results go only in [robot.md](docs/robot.md#results), and only from a supervised session reported by a person.
- Importing a skill module must do nothing; `skills` never imports `dispatcher`, and third-party imports live inside functions in `real.py` ([skills.md](docs/skills.md#no-side-effects-on-import)).
- Exactly one JSON line on stdout, via `result.emit()`; everything else goes to stderr ([skills.md](docs/skills.md#invocation)).
- Skills wait through `backend.sleep()`, never `time.sleep`, so the stub time scale applies ([skills.md](docs/skills.md#stub-backend)).
- Skills apply no defaults; the dispatcher fills them. Run by hand, pass every param ([skills.md](docs/skills.md#running-a-skill-by-hand)).
