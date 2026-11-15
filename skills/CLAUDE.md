# skills/CLAUDE.md

The `skills` package: the `SKILL.md` catalog, the skills themselves, and the skill runtime (`runner.py`, `motion.py`). The contract the skills follow, including the process runtime they run on (`contract/runtime.py`), is the `contract` package ([contract/CLAUDE.md](../contract/CLAUDE.md)); the robot service they move and read state through is the `services.robot` package ([services/robot/CLAUDE.md](../services/robot/CLAUDE.md)); the vision service `detect_object` asks is the `services.vision` package ([services/vision/CLAUDE.md](../services/vision/CLAUDE.md)). Repo-wide rules are in the root [CLAUDE.md](../CLAUDE.md).

## Commands

```bash
uv run pytest skills -q                        # skills unit tests only
uv run pytest -m integration                   # after touching a skill's subprocess behaviour or the response format
uv run pytest --update-golden                  # after a SKILL.md change; then git diff dispatcher/tests/golden
uv run go2 catalog                             # after touching the catalog: check catalog and registry hash
uv run go2 --reset-stub                        # stub checks: back to standing (sit persists across tasks)
```

## Docs

| Doc | Covers |
|---|---|
| [skills.md](docs/skills.md) | The skills, catalog and registry hash, import rules, adding a skill, running one by hand |
| [vision.md](../services/vision/docs/vision.md) | The vision service, its client surface, the stub camera, object detection on the real backend |
| [contract.md](../contract/docs/contract.md) | `SKILL.md` frontmatter and body, invocation and environment, response schema, error codes, timeout and motion-cost policies, the process runtime (`contract/runtime.py`), wire framing, shared constants |
| [service.md](../services/robot/docs/service.md) | The robot service, its client surface, the lease, the stub backend, fault injection |
| [robot.md](../services/robot/docs/robot.md) | Robot facts, SDK and DDS usage, state sampling, posture rule, settle waits, real backend, supervised robot checklist |
| [safety.md](../docs/safety.md) | Stop path, motion budget, bounds, supervised-operation rules |
| [configuration.md](../docs/configuration.md) | `robot.*` and `stub.*` config keys, `.env` |
| [setup.md](../docs/setup.md) | Installing the `robot` and `vision` extras, network interface |
| [testing.md](../tests/docs/testing.md) | Test layout, markers, fault injection in tests, robot checks |

## Gotchas

- The docs define the skill contract; code follows [contract.md](../contract/docs/contract.md) and [skills.md](docs/skills.md), not the other way round.
- `SKILL.md` frontmatter and body are fixed texts: any wording change changes the catalog, the registry hash and `catalog.txt`, and makes runs incomparable. Regenerate with `--update-golden` and review the diff ([skills.md](docs/skills.md#catalog-and-registry-hash)).
- The `SKILL.md` body is model-facing and parsed against a fixed section template with a length cap; human notes go in the docs, never in a `SKILL.md` ([contract.md](../contract/docs/contract.md#skillmd-body)).
- An agent never runs a skill on the real backend: no `--backend real`, no `robot.backend = "real"`, no `--run-robot` ([safety.md](../docs/safety.md#supervised-operation-rules)).
- Mark robot facts not yet checked on the robot as *unverified*. Checklist results go only in [robot.md](../services/robot/docs/robot.md#results), and only from a supervised session reported by a person.
- Importing a skill module must do nothing; `skills` imports `contract` and the client surfaces of `services.robot` (`protocol`, `client`, `ramp`, `faults`, `stub_state`) and `services.vision` (`protocol`, `client`, `coco`) but never `dispatcher`, `contract.manifest`, a backend, a detector or a service ([skills.md](docs/skills.md#no-side-effects-on-import)).
- Exactly one JSON line on stdout, via `contract.runtime.emit()`; everything else goes to stderr ([contract.md](../contract/docs/contract.md#invocation)).
- Skills wait through `backend.sleep()`, never `time.sleep`, so the stub time scale applies ([service.md](../services/robot/docs/service.md#stub-backend)).
- Skills apply no defaults; the dispatcher fills them. Run by hand, pass every param ([skills.md](docs/skills.md#running-a-skill-by-hand)).
