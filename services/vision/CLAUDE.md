# services/vision/CLAUDE.md

The `vision` package: the vision service, its protocol and client, the real detector (front camera + YOLO), the stub camera, the box classification and the COCO class list. The contract it follows is the `contract` package ([contract/CLAUDE.md](../../contract/CLAUDE.md)). Repo-wide rules are in the root [CLAUDE.md](../../CLAUDE.md).

## Commands

```bash
uv run pytest services/vision -q                        # vision unit tests only
uv run pytest -q services/vision skills contract        # after touching the protocol, the client or the class list: detect_object uses them
uv run pytest -m integration                   # after touching the service or the stub camera
uv run lint-imports                            # import contracts; must pass before a commit
```

## Docs

| Doc | Covers |
|---|---|
| [vision.md](docs/vision.md) | The package's modules and client surface, the vision service and its protocol, the lifecycle contract, the stub camera, object detection on the real backend, the front camera |
| [contract.md](../../contract/docs/contract.md) | Response schema, error codes, process environment, `process` and `backend`, wire framing |
| [skills.md](../../skills/docs/skills.md) | The `detect_object` skill, running the service and a skill by hand |
| [service.md](../robot/docs/service.md) | The stub world: state file, objects, fault injection |
| [robot.md](../robot/docs/robot.md) | Robot facts, SDK and DDS usage, the camera calls, supervised robot checklist |
| [safety.md](../../docs/safety.md) | Supervised-operation rules |
| [configuration.md](../../docs/configuration.md) | `robot.yolo_weights`, `stub.objects`, `.env` |
| [setup.md](../../docs/setup.md) | Installing the `vision` extra, the weights file |
| [testing.md](../../tests/docs/testing.md) | Test layout, markers, robot checks |

## Gotchas

- Real-backend code (`detectors/real.py`) cannot be run from an agent: no `--backend real`, no `robot.backend = "real"`, no `--run-robot`. Verify it with unit tests and the stub only ([safety.md](../../docs/safety.md#supervised-operation-rules)).
- Mark camera and detector facts not yet checked on the robot as *unverified* ([vision.md](docs/vision.md#object-detection)).
- `skills` and `dispatcher` import only `services.vision.protocol`, `services.vision.client` and `services.vision.coco`; these modules never import a detector, the service or a third-party package ([vision.md](docs/vision.md#the-package)).
- `vision` imports `contract` only. The two exceptions: `detectors/stub.py` imports `services.robot.stub_state` and `detectors/real.py` imports `services.robot.backends.real` for `init_dds` ([vision.md](docs/vision.md#design-decisions)). `robot` never imports `vision`.
- Third-party imports (`ultralytics`, `cv2`, `numpy`, `unitree_sdk2py`) live inside methods in `detectors/real.py`; importing any `vision` module needs only the standard library.
- The weights file is never downloaded automatically; a missing file is `weights_missing`.
- The service only reads the camera: it never creates a `SportClient`.
- The dispatcher starts the service by `protocol.SERVICE_MODULE` and never imports `services.vision.service`.
- The box thresholds in `boxes.py` are shared by both detectors; a change there changes stub and real results together.
