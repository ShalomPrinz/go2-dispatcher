# Shared backend code

The `services.backend` package: the code both services use for the backend they run on, the stub state file and the one DDS init per process. The robot service and its sim are in [service.md](../../robot/docs/service.md), the SDK and the real backend in [robot.md](../../robot/docs/robot.md), the stub camera and the real detector in [vision.md](../../vision/docs/vision.md); the stub world's format (postures, conditions, objects, defaults) is `contract.stub` ([contract.md](../../../contract/docs/contract.md#the-package)).

## The package

| Module | Holds |
|---|---|
| `services/backend/stub_state.py` | The stub state file: `Pose`, `Command`, `StubState`, `state_file()`, `read_state`, `read_posture`, `write_state`, the field writers (`write_posture`, `write_pose`, `write_command`) and `reset` |
| `services/backend/dds.py` | `init_dds()` and `dds_init_ms()`: DDS init once per process and its time |

Each module needs only the standard library and `contract`; the SDK import in `dds.py` is inside `init_dds`. `services.backend` imports no other project package (`services.robot`, `services.vision`, `skills`, `dispatcher`). Its importers are the robot package (the sim, the real backend, the utilities, and the dispatcher's stub control `services.robot.stub`) and the vision detectors. Skills do not import it, and the dispatcher reaches it only through `services.robot.stub.reset_stub`.

## Stub state file

The stub keeps posture, an odometry pose and the running velocity command in one JSON file at `GO2_STUB_STATE_FILE` (`stub.state_file`, [configuration.md](../../../docs/configuration.md)): `{"posture": "standing" | "sitting", "pose": {"x": m, "y": m, "yaw": rad}, "command": [vx, vy, vyaw] | null, "sequence_s": s, "condition": "fallen" | "stuck" | null}`. The pose is in the world frame: the robot starts at (0, 0) facing +x, and yaw is counter-clockwise. A missing file means standing at the origin; a missing `pose` means the origin, a missing `command` or `condition` none and a missing `sequence_s` 0. Any other malformed content raises `ValueError` naming the file. `condition` is an injected [robot fault](../../robot/docs/service.md#robot-faults); `sequence_s` is the [start ramp's](../../robot/docs/service.md#stub-backend) progress.

Every writer reads the file, changes its own fields and replaces the file atomically (temp file in the same folder + `os.replace`), creating a missing parent folder. `write_command` starts a new `Move` sequence (`sequence_s` 0, no `condition`) when a command is set from none or cleared, and does nothing on a fallen robot. `reset(posture, path)` writes `posture` at the origin with no command and no condition, and rejects a posture outside `STUB_POSTURES`.

The writers are the robot service's `SimRobot`, whose lock serialises its threads, `stop_move`'s own `SimRobot`, and `reset`, which the dispatcher calls through `services.robot.stub.reset_stub(posture, path)`; no cross-process lock is needed because `stop_move` and `reset` run only while the service is idle, stopped, hung or dead. The stub camera only reads the pose.

## DDS init

`init_dds()` calls `ChannelFactoryInitialize(DDS_DOMAIN_ID, $GO2_IFACE)` (domain 0) once per process, under a lock, before anything touches DDS; later calls return at once. An empty `GO2_IFACE` raises `BackendNotConfigured` (`backend_not_configured`) without an attempt. `dds_init_ms()` is the time spent in `init_dds` in this process, including the import of the SDK channel module and failed attempts, or `None` if it was never attempted; a utility reports it as `timing.dds_init_ms` ([contract.md](../../../contract/docs/contract.md#response-schema)). Its callers are the robot's real backend (state subscription and sport client), the utilities on the real backend, and the real detector ([robot.md](../../robot/docs/robot.md#sdk-and-dds-usage)).

## Design decisions

- **What both services need for the backend they run on is one package, `services.backend`, beside them under `services/`.** The stub state file is written by the robot's sim and the dispatcher's reset and read by the stub camera; DDS may be initialised only once per process, and the robot backend and the real detector both need it. Each has one owner that neither service has to load the other for, so `services.robot` and `services.vision` stay independent with no exception in the import contracts. It needs only the standard library and `contract`, so loading it loads no backend, no service and no SDK. Rejected: keeping it in `services.robot` with vision importing it (an exception to the services' independence, and vision loading robot code); putting it in `contract` (skills import `contract`, which stays free of the sim world and of DDS).
- **The dispatcher resets the stub through `services.robot.stub.reset_stub(posture, path)`, not by importing `services.backend`.** The reset is a robot-side operation on the dispatcher's client surface, so the dispatcher's import graph holds no backend-side module; the module is separate from `client` so skills, which import `client`, never reach the stub world ([service.md](../../robot/docs/service.md#design-decisions)). The state file layout stays a stub internal: a richer stub state changes `reset` alone.
