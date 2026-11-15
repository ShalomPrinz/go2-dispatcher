# services/backend/CLAUDE.md

The `backend` package: what the robot and vision services share for the backend they run on, the stub state file and the one DDS init per process. Owned by `robot-dev`; `vision` only imports it. Repo-wide rules are in the root [CLAUDE.md](../../CLAUDE.md).

## Commands

```bash
uv run pytest services/backend -q                      # backend unit tests only
uv run pytest -q services skills contract              # after touching the stub state file: the sim, the stub camera and the dispatcher's reset use it
uv run pytest -m integration                           # after touching the stub state file
uv run lint-imports                                    # import contracts; must pass before a commit
```

## Docs

| Doc | Covers |
|---|---|
| [backend.md](docs/backend.md) | The package's modules and importers, the stub state file format and writers, DDS init and `dds_init_ms` |
| [service.md](../robot/docs/service.md) | The sim that writes the state file, robot faults, the dispatcher's `stub.reset_stub` |
| [robot.md](../robot/docs/robot.md) | SDK and DDS usage on the robot |
| [vision.md](../vision/docs/vision.md) | The stub camera and the real detector, which import this package |
| [contract.md](../../contract/docs/contract.md) | The stub world format (`contract.stub`), `backend_not_configured`, `timing.dds_init_ms` |

## Gotchas

- `services.backend` imports the standard library and `contract` only, never `services.robot`, `services.vision`, `skills` or `dispatcher` ([backend.md](docs/backend.md#the-package)).
- The SDK import in `dds.py` stays inside `init_dds`; DDS init on the real backend cannot be run from an agent ([safety.md](../../docs/safety.md#supervised-operation-rules)).
- A change to the state file layout changes what the sim, the stub camera and the dispatcher's reset see; update [backend.md](docs/backend.md#stub-state-file) in the same commit.
