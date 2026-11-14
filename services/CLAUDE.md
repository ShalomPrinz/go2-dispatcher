# services/CLAUDE.md

The `services` package: the two long-lived processes the dispatcher supervises, the robot service ([robot/CLAUDE.md](robot/CLAUDE.md)) and the vision service ([vision/CLAUDE.md](vision/CLAUDE.md)). Each subpackage has its own docs, tests, `CLAUDE.md` and agent (`robot-dev`, `vision-dev`); this folder holds only this file and an empty `__init__.py`. Repo-wide rules are in the root [CLAUDE.md](../CLAUDE.md).

## Commands

```bash
uv run pytest -q services                      # unit tests of both services
uv run pytest -m integration                   # after touching either service: real processes on the stub backend
uv run lint-imports                            # import contracts; must pass before a commit
```

## Docs

| Doc | Covers |
|---|---|
| [service.md](robot/docs/service.md) | The robot package, the robot service and its lease, the stub backend, fault injection |
| [robot.md](robot/docs/robot.md) | Robot facts, SDK and DDS usage, the real backend, supervised robot checklist |
| [vision.md](vision/docs/vision.md) | The vision package, the vision service, the stub camera, object detection on the robot |
| [architecture.md](../docs/architecture.md) | Why the services are grouped here, and the package dependency directions |
| [testing.md](../tests/docs/testing.md) | Import contracts and their named exceptions |

## Gotchas

- `services.robot` and `services.vision` import `contract`, never `skills` or `dispatcher`, and never each other; the few edges outside that are named exceptions in `pyproject.toml` ([testing.md](../tests/docs/testing.md#import-boundaries)).
- Each service keeps its own protocol and client; the shared framing is `contract.wire`. Backend selection and the process helpers live in `contract`, because skills use them.
