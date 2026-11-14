# Vision

The `vision` package: the vision service, its protocol and client, the detectors (YOLO on the real front camera, and the stub camera), the box classification and the COCO class list. The `detect_object` skill that asks the service is in [skills.md](../../skills/docs/skills.md#the-skills); the stub world and its state file are in [service.md](../../robot/docs/service.md#stub-backend); the contract is in [contract.md](../../contract/docs/contract.md).

## The package

| Module | Holds |
|---|---|
| `vision/protocol.py` | The wire protocol: `MAX_MESSAGE_BYTES`, the replies (`ok_reply`, `error_reply`, `parse_reply`), `DetectResult`, the detector errors (`DetectorError`, `CameraUnavailable`, `BadFrame`, `WeightsMissing`, `VisionUnavailable`, `ServiceError`), `error_message`, and the service's lifecycle constants (`SERVICE_MODULE`, `READY_LINE`, `STARTUP_TIMEOUT_S`, `STOP_GRACE_S`) |
| `vision/client.py` | The client: `socket_path`, `connect`, `detect` |
| `vision/coco.py` | `COCO_CLASSES`, the 80 detectable class names in YOLO order |
| `vision/service.py` | The service process: detector selection, the request handler, the listener |
| `vision/boxes.py` | `classify_box` and its thresholds: a box to position and closeness |
| `vision/detectors/real.py` | `RealDetector`: a front-camera frame through `unitree_sdk2py`'s `VideoClient`, and YOLO ([object detection](#object-detection)) |
| `vision/detectors/stub.py` | `StubDetector` and `project`: the [stub camera](#stub-camera) |

**Client surface.** `skills` and `dispatcher` import only `vision.protocol`, `vision.client` and `vision.coco`. None of these loads a detector, the service, torch, `ultralytics`, `cv2`, pydantic or PyYAML. `vision` imports `contract` only, with two exceptions: `vision.detectors.stub` imports `robot.stub_state`, and `vision.detectors.real` imports `robot.backends.real` ([design decisions](#design-decisions)). `robot` never imports `vision`. Third-party imports (`ultralytics`, `cv2`, `numpy`, `unitree_sdk2py`) are inside the methods of `RealDetector`, so importing any `vision` module needs only the standard library.

## Vision service

`detect_object` does not load a detector itself. It asks the vision service (`vision/service.py`), a long-running process that loads the detector once, warms it up and answers requests on a Unix socket. On the real backend that is `RealDetector` (`vision/detectors/real.py`: YOLO plus a `VideoClient` kept open across requests, [object detection](#object-detection)); on the stub it is `StubDetector`, the simulated camera ([stub camera](#stub-camera)). The service only reads the camera: it never creates a `SportClient` and never commands motion. It grabs a frame only when a request arrives.

**Protocol** (`vision/protocol.py`, client in `vision/client.py`). `multiprocessing.connection` over the Unix socket at `GO2_VISION_SOCKET`, one request and one reply per connection, each a UTF-8 JSON object sent with `send_bytes` (never pickle, [contract.md](../../contract/docs/contract.md#wire-framing)), at most `MAX_MESSAGE_BYTES`:

- Request: `{"target": "<COCO class>"}`. The skill checks the target first; the service does not.
- Reply: the `DetectResult` fields `{"found": bool, "position", "closeness", "confidence"}` (the last three `null` when not found), or `{"error": {"code": "<error code>", "message": "<ExceptionType>: <message>"}}` with `camera_unavailable`, `bad_frame`, `weights_missing`, `backend_not_configured` or `detector_error`.

In `detect_object`, `connect_ms` is the connect and `exec_ms` the request until the reply. The service answers one connection at a time and drops a connection that sends no request within `REQUEST_WAIT_S` (1.0 s). If warm-up fails (weights missing, camera client not created), the service becomes ready all the same and logs the error to stderr; each request retries the part that failed and replies with its error code, so the model sees `weights_missing` rather than a dead service.

**Lifecycle contract** (owned by the dispatcher; the constants are in `vision/protocol.py`):

- **Start:** `python -m vision.service` (`protocol.SERVICE_MODULE`), no arguments, working directory the base dir, environment `child_env(...)` with `fault` unset: the same call the executor makes for a skill, so `GO2_VISION_SOCKET` is `vision_socket_path(os.getpid())` = `<tempfile.gettempdir()>/go2-vision-<dispatcher pid>.sock` on both sides. Start it in its own session (`start_new_session=True`), with stdin `/dev/null`, stdout a pipe, and stderr (its log) to a file or inherited, never an unread pipe.
- **Ready:** the service writes exactly one line, `ready` (`READY_LINE`), to stdout once it is warmed up and listening; nothing else is ever written to stdout. Wait for it up to `STARTUP_TIMEOUT_S` (30.0 s, *tunable*). Exit before the line is a failed start; exit code 2 means `GO2_BACKEND` or `GO2_VISION_SOCKET` is missing or unknown.
- **Restart:** before each `detect_object` step, if the process has exited, start it again and wait for `ready`. A step whose service is down or slow is not retried: `detect_object` reports `vision_unavailable`.
- **Stop:** `SIGTERM`; it closes the listener, removes the socket file and exits 0. Send `SIGKILL` if it is alive after `STOP_GRACE_S` (2.0 s).
- **Socket file:** created with mode `0600`. A file left by a killed service is removed by the next service at start.
- **Parent death:** the service runs the orphan watchdog (`contract.process.start_orphan_watchdog`, `GO2_PARENT_PID`): when its parent is gone it exits with code 137 within about 2.2 s, leaving the socket file for the next start to remove. Without `GO2_PARENT_PID` (a manual run) it runs until stopped.

Running the service by hand is in [skills.md](../../skills/docs/skills.md#running-a-skill-by-hand).

## Stub camera

On the stub backend the `robot` package simulates the robot and owns the state file and the objects in `GO2_STUB_OBJECTS` ([service.md](../../robot/docs/service.md#stub-backend)); the camera is simulated here (`vision/detectors/stub.py`), at the pose in that state file.

`StubDetector`, served by the [vision service](#vision-service) with the service's own env: `detect(target)` waits `DETECT_S`, then projects the object listed under `target` in `GO2_STUB_OBJECTS` through a pinhole camera `CAMERA_OFFSET_M` ahead of the body centre (`CAMERA_WIDTH_PX` × `CAMERA_HEIGHT_PX`, horizontal field of view `CAMERA_HFOV_DEG`, square pixels; [front camera](#object-detection)). The box centre-x fraction is `0.5 − f × tan(bearing) / width`, so a positive bearing (left) gives a small x; the box height fraction is `f × height_m / depth / height`, clipped to 1, where depth is the distance along the camera axis. The box goes to the real detector's classifier, `boxes.classify_box`. Not found when the target is not listed, is behind the camera, is outside the horizontal field of view, or its box height fraction is below `MIN_BOX_H`. Confidence is deterministic: `CONFIDENCE_MIN` + (`CONFIDENCE_MAX` − `CONFIDENCE_MIN`) × box height fraction.

The stub's camera fault is applied in the skill process, before the request, so the service never sees it ([fault injection](../../robot/docs/service.md#fault-injection)).

## Object detection

`detect_object` on the real backend asks the [vision service](#vision-service), which takes one frame from the front camera per request and runs YOLO on it (`ultralytics`, weights from `robot.yolo_weights`):

- At start the service loads YOLO, runs one predict on a blank `IMGSZ` × `IMGSZ` image (the first predict is slow) and creates the `VideoClient`; it keeps both for its lifetime. A part that failed at start is retried on the next request. Expected warm detection time is one frame RPC plus about 0.04 s of inference (*unverified* on the robot).
- The weights file is checked before the camera is touched and is **never downloaded automatically**; a missing file is `weights_missing`.
- Inference at `IMGSZ` with confidence threshold `CONF`. Of the boxes whose class equals the target, the one with the highest confidence is used.
- **Position** from the box centre's horizontal fraction of the image (`LEFT_MAX_X`, `RIGHT_MIN_X`); **closeness** from the box height's fraction (`NEAR_MIN_H`, `MEDIUM_MIN_H`).
- Confidence is rounded to `CONFIDENCE_DECIMALS` decimals.

The box classification and its thresholds live in `vision/boxes.py` (`classify_box`), shared with the stub's simulated camera ([stub camera](#stub-camera)); `IMGSZ`, `CONF` and `CAMERA_TIMEOUT_S` stay on `RealDetector`. All are *tunable* and *unverified* on the robot's camera.

**Front camera.** Unitree's Go2 specification gives the front camera as 1280×720 with an ultra-wide 120° field of view; the stub assumes the 120° is horizontal, square pixels, no distortion and a camera about 0.3 m ahead of the body centre (`CAMERA_*` in `vision/detectors/stub.py`). All *unverified*. Under that model the focal length is about 370 px and a 0.25 m bottle reads `near` only within about 0.21 m camera depth, `medium` up to about 0.43 m and `far` beyond, and the `center` band is about ±19° of bearing. The closeness thresholds are therefore likely too coarse for objects of bottle size; calibrating them is pending human work ([roadmap.md](../../docs/roadmap.md#pending-human-work)).

## Design decisions

- **Detection runs in a long-running vision service; `detect_object` is a short process that asks it.** Loading torch and YOLO, the first predict, DDS init and `VideoClient.Init` took about 5 s of every detection on the lab machine, while a warm predict takes 0.04 s. The service pays that once per dispatcher run. The one-subprocess-per-call rule ([skills.md](../../skills/docs/skills.md#design-decisions)) holds for the skill: `detect_object` is one process per call with the same response, timeout and kill path, and the service never touches the sport client, so the clean-channel and kill reasons, which are about motion, do not apply to it. Requests carry only a target and the service grabs a frame per request, so no state leaks between calls. IPC is `multiprocessing.connection` with JSON bytes, standard library only. Rejected: exporting YOLO to ONNX to drop torch from the skill process (a model load and first inference per call all the same, and a second model format to maintain); streaming frames in the background (camera traffic and a stale-frame question for no gain, since a frame costs one RPC per request); retrying `GetImageSample` on error 3104.
- **`skills` and `dispatcher` import three `vision` modules: `protocol`, `client` and `coco`.** They are what a client needs: the result, the errors and the limits, the connection, and the class list that `detect_object` checks a target against and the dispatcher's config checks stub objects against. Each needs only the standard library and `contract`, so a skill process and the dispatcher load no detector, no service code, no torch and no SDK. Rejected: one flat package where any module may be imported (a skill could reach `detectors.real` and torch through an innocent-looking import).
- **The service's lifecycle constants and `SERVICE_MODULE` are in `protocol.py`.** The dispatcher starts the service with `python -m` and `SERVICE_MODULE`, waits for `READY_LINE` within `STARTUP_TIMEOUT_S` and stops it with `STOP_GRACE_S`, all without importing the service. Rejected: importing `vision.service` for them, which puts the service and, through `make_detector`, the detector stack on the dispatcher's import chain.
- **The detector's errors and `DetectResult` are in `protocol.py`.** The replies are built from them on the service side and parsed into them on the client side, and the detectors raise and return them, so the one module every side imports holds them.
- **`vision.detectors.stub` imports `robot.stub_state`.** The stub camera looks at the stub world from the stub robot's pose: `read_state().pose` and `objects()`, with the `Pose` and `WorldObject` types. `robot.stub_state` owns that file and those objects ([service.md](../../robot/docs/service.md#design-decisions)) and needs only the standard library and `contract`. Rejected: a second copy of the world objects and their parsing in `vision` (two readers of `GO2_STUB_OBJECTS` that can drift, and a camera that sees a different world than the sim's obstacle distances).
- **`vision.detectors.real` imports `robot.backends.real` for `init_dds`.** A process initialises DDS once, and `init_dds` is the one place that does it, with the interface from `GO2_IFACE` ([robot.md](../../robot/docs/robot.md)). The import is inside the vision service only; `RealDetector` creates a `VideoClient` and never a `SportClient`. Rejected: a second DDS init in `vision` (two copies of the interface handling and of the once-per-process guard).
- **The stub camera reuses the real detector's classifier and simulates only the image.** A pinhole projection over the objects placed in the stub world stands in for the camera image and YOLO; position and closeness come from the same thresholds as on the robot (`vision/boxes.py`), so the two backends cannot drift apart. Detections change as the robot moves (a chair behind the robot appears after a turn), because the pose comes from the sim ([service.md](../../robot/docs/service.md#stub-backend)). The projection ignores lens distortion, occlusion and the object's vertical position, and the pose resets only with the posture, never between tasks. Rejected: a static `{class: "position:closeness"}` table (`stub.detections`), alone or beside the world, because it answers the same whatever the robot does, so a plan that turns or walks before looking is never tested against what it would actually see.
