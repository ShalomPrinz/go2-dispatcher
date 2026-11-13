# contract/CLAUDE.md

The `contract` package: what the dispatcher, the skills and the services agree on. The `SKILL.md` schema and parser, the skill process environment, the response and robot state schema, the error codes, the policy types, the shared wire framing and the shared constants. Repo-wide rules are in the root [CLAUDE.md](../CLAUDE.md).

## Commands

```bash
uv run pytest contract -q                      # contract unit tests only
uv run pytest -q skills contract               # after any contract change: the skills read it at every spawn
uv run pytest -m integration                   # after touching the response schema, the environment or the wire framing
uv run pytest --update-golden                  # after a manifest schema change that alters the catalog; then git diff dispatcher/tests/golden
uv run go2 catalog                             # check the catalog and registry hash still match
uv run lint-imports                            # import contracts; must pass before a commit
```

## Docs

| Doc | Covers |
|---|---|
| [contract.md](docs/contract.md) | The package's modules, `SKILL.md` frontmatter, expectations and preconditions, `SKILL.md` body, invocation and environment, response schema, error codes, timeout and motion-cost policies, wire framing, shared constants |
| [skills.md](../skills/docs/skills.md) | The skills, catalog and registry hash, stub and fault injection, robot and vision services (their protocols use the wire framing), adding a skill |
| [robot.md](../skills/docs/robot.md) | Robot state fields and the posture rule behind `RobotState` and `Posture` |
| [loop-and-context.md](../dispatcher/docs/loop-and-context.md) | How the dispatcher uses the contract: step bounds, verification, step outcomes |
| [testing.md](../tests/docs/testing.md) | Test layout, import boundaries |

## Gotchas

- The docs define the contract; code follows [contract.md](docs/contract.md), not the other way round.
- `contract` imports no other project package: never `skills`, never `dispatcher` ([contract.md](docs/contract.md#the-package)).
- Standard library only, at top level and inside functions; skill processes import these modules at every spawn. The one exception is `manifest.py` (pydantic, PyYAML), which only the dispatcher process and tests import ([contract.md](docs/contract.md#design-decisions)).
- The response schema, error codes, `GO2_*` names, socket and lock paths and the wire framing are read by both sides and by recorded run logs: a change is a contract change, documented in the same commit ([contract.md](docs/contract.md#response-schema)).
- A manifest schema change that alters what the catalog renders changes the registry hash and makes runs incomparable ([skills.md](../skills/docs/skills.md#catalog-and-registry-hash)).
- `constants.py` takes a value only when a second package would otherwise import a service, client, backend or stub module just for it ([contract.md](docs/contract.md#design-decisions)).
- `contract/tests/` imports neither `skills` nor `dispatcher`; a test that needs a skill module or the catalog belongs in `skills/tests/`.
