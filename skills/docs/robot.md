# The robot

The Go2 EDU side of the system: what the code assumes about the robot, how it talks to it through the Unitree SDK over DDS, how state is sampled and turned into a posture, and the supervised checklist that verifies all of this on the robot. The skill contract (invocation, response schema, error codes, policies, stub backend) is in [skills.md](skills.md); stopping the robot is in [safety.md](../../docs/safety.md); installing the SDK is in [setup.md](../../docs/setup.md).

**Status: the real backend has run on the robot under supervision** (26 tasks on 2026-10-29, normal CLI from the lab machine). The [checklist](#supervised-robot-checklist) is partly worked through: the [results](#results) table lists what was measured and the [known issues](#known-issues) the causes of what failed. Everything else on this page that depends on the robot's behaviour stays *unverified*.

## Robot facts

- **Unitree Go2 EDU**, controlled directly through the Unitree SDK2 Python bindings (`unitree_sdk2py`) over CycloneDDS. No ROS.
- Reached over a wired Ethernet interface, on the robot's subnet (usually `192.168.123.x`; the robot is `192.168.123.161`). The interface name is a config value (`robot.network_interface`), never hardcoded ([setup.md](../../docs/setup.md#2-network-interface)).
- DDS domain 0.
- The IMU and sport mode are assumed available through `SportModeState` on topic `rt/sportmodestate` (*unverified*).
- **SDK action calls return when the command is accepted, not when the motion ends.** This is why `sit` and `stretch` wait after their call ([settle waits](#settle-waits)).
- The robot's low-level contact person is Achiya ([project.md](../../docs/project.md)); ask him about SDK topics and state fields. Odometry is available relative to the power-on pose; its source and accuracy are *unverified* (checklist item 2).

## SDK and DDS usage

All SDK use is in `skills/real.py` and, for the camera, `skills/real_detector.py` (run only by the [vision service](skills.md#vision-service)), selected in the skill process by `GO2_BACKEND=real` (`backend.py`). The dispatcher itself never imports the SDK ([architecture.md](../../docs/architecture.md#process-model)); every SDK import is inside a function, so importing any `skills` module needs only the standard library ([skills.md](skills.md#no-side-effects-on-import)).

| What | How |
|---|---|
| DDS init | `ChannelFactoryInitialize(0, $GO2_IFACE)`, once per process, before anything touches DDS. An empty `GO2_IFACE` gives `backend_not_configured`. |
| Sport client | `SportClient()`, `SetTimeout(SPORT_CLIENT_TIMEOUT_S)` (5.0 s, tunable), `Init()`. One per skill process. A call that waits for a reply (`StopMove`, `StandDown`, `Stretch`) returns a non-zero code after this long without one; it must fit inside the skill's timeout ([skills.md](skills.md#policies)). |
| Calls used | `Move(vx, vy, vyaw)`, `StopMove()`, `StandDown()`, `Stretch()`. Each returns an int; 0 is success, anything else is an `sdk_error` with the code in `observations.sdk_ret`. |
| Camera | `VideoClient`, `SetTimeout(CAMERA_TIMEOUT_S)`, `Init()` once in the vision service, then `GetImageSample()` → JPEG bytes per request, decoded with OpenCV. Error 3104 and any other non-zero code is `camera_unavailable`, never retried. |
| State | `ChannelSubscriber("rt/sportmodestate", SportModeState_)`, queue length `SUBSCRIBER_QUEUE_LEN` ([state sampling](#state-sampling)). |

Constants are module-level in `skills/real.py`, except the detector ones ([object detection](#object-detection)). Each skill call and utility is a fresh process, so DDS is initialised once per call ([skills.md](skills.md#design-decisions)); the vision service initialises it once per dispatcher run.

### Motion

`walk` and `turn` are open-loop: they send `Move` every `CMD_PERIOD_S` (shared, in `motion.py`) at `VELOCITY_MPS` (in `walk.py`) or `YAW_RATE_RPS` (in `turn.py`) for the travel time plus a fixed lead time `LEAD_S` (per skill, in `walk.py` and `turn.py`), then `StopMove()`. The lead time makes up for the start ramp of each `Move` sequence (1.1 s for walk, 0.2 s for turn, both tunable). Nothing measures the distance or angle covered. If `Move` returns non-zero, the loop sends `StopMove()` and reports `sdk_error`. If the dispatcher dies, the loop notices on its next iteration, stops the robot and exits ([safety.md](../../docs/safety.md#if-the-dispatcher-dies)).

`sit` is `StandDown()` (as in the predecessor skill, not `Sit()`) and `stretch` is the robot's built-in `Stretch()` routine, each followed by a settle wait.

### Object detection

`detect_object` on the real backend asks the [vision service](skills.md#vision-service), which takes one frame from the front camera per request and runs YOLO on it (`ultralytics`, weights from `robot.yolo_weights`):

- At start the service loads YOLO, runs one predict on a blank `IMGSZ` × `IMGSZ` image (the first predict is slow) and creates the `VideoClient`; it keeps both for its lifetime. A part that failed at start is retried on the next request. Expected warm detection time is one frame RPC plus about 0.04 s of inference (*unverified* on the robot).
- The weights file is checked before the camera is touched and is **never downloaded automatically**; a missing file is `weights_missing`.
- Inference at `IMGSZ` with confidence threshold `CONF`. Of the boxes whose class equals the target, the one with the highest confidence is used.
- **Position** from the box centre's horizontal fraction of the image (`LEFT_MAX_X`, `RIGHT_MIN_X`); **closeness** from the box height's fraction (`NEAR_MIN_H`, `MEDIUM_MIN_H`).
- Confidence is rounded to `CONFIDENCE_DECIMALS` decimals.

The box classification and its thresholds live in `skills/vision.py` (`classify_box`), shared with the stub's simulated camera ([skills.md](skills.md#stub-backend)); `IMGSZ`, `CONF` and `CAMERA_TIMEOUT_S` stay on `RealDetector`. All are *tunable* and *unverified* on the robot's camera.

**Front camera.** Unitree's Go2 specification gives the front camera as 1280×720 with an ultra-wide 120° field of view; the stub assumes the 120° is horizontal, square pixels, no distortion and a camera about 0.3 m ahead of the body centre (`CAMERA_*` in `skills/stub.py`). All *unverified*. Under that model the focal length is about 370 px and a 0.25 m bottle reads `near` only within about 0.21 m camera depth, `medium` up to about 0.43 m and `far` beyond, and the `center` band is about ±19° of bearing. The closeness thresholds are therefore likely too coarse for objects of bottle size; calibrating them is pending human work ([roadmap.md](../../docs/roadmap.md#pending-human-work)).

## State sampling

Robot state is sampled:

- at the start and end of every skill call (`state_before`, `state_after`);
- by the `stop_move` utility after `StopMove()` and its [settle wait](#settle-waits) (`state_after`);
- by the `read_state` utility: `go2 state`, and at startup on the real backend to set the initial posture (`unknown` if it fails).

**How.** On the first sample in a process, the backend subscribes to `rt/sportmodestate`. A sample waits up to `STATE_WAIT_S` for a message that arrived *after* the sample was requested; if none arrives, it uses the latest earlier message; if there is none at all, the sample fails. A failed sample does not fail the step: it is reported in `state_error` and the state is `null` ([skills.md](skills.md#response-schema)).

**Fields.** Each sample is a `RobotState` (`skills/schema.py`), which rejects unknown keys, so a new field is added there first ([skills.md](skills.md#response-schema)):

| Field | Source in `SportModeState` |
|---|---|
| `t` | local `time.time()` when the sample was mapped |
| `backend` | `"real"` |
| `posture` | derived ([posture rule](#posture-rule)) |
| `mode`, `gait_type` | `mode`, `gait_type` |
| `body_height` | `body_height` (m) |
| `position`, `velocity` | `position`, `velocity` (3 values each); the robot's own estimate, not used in v1 ([odometry](#open-robot-side-questions)) |
| `yaw_speed` | `yaw_speed` |
| `imu_rpy` | `imu_state.rpy` (roll, pitch, yaw) |
| `foot_force` | `foot_force` (4 values) |
| `error_code` | `error_code` |
| `range_obstacle_raw` | `range_obstacle` (4 values) as received, zeros included; only a non-finite or non-numeric element becomes `null` (JSON has no NaN), and the whole field is `null` if the length is not 4 |
| `obstacle_m` | `{front, back, left, right}` in metres, derived from `range_obstacle_raw` by `derive_obstacles` in `skills/real.py`; a value of 0 or below, above `RANGE_OBSTACLE_MAX_M` (30 m, *tunable*) or `null` becomes `null` (unknown) |

numpy and ctypes values are converted to plain numbers. A missing attribute or a non-finite value becomes `null`; a list field other than `range_obstacle_raw` becomes `null` as a whole if any element is bad or its length is wrong.

**Obstacle distances are *unverified*.** Unitree documents neither the units, the index order nor when firmware fills `range_obstacle` (the SDK IDL and `unitree_ros2`'s `SportModeState.msg` declare only `float32[4] range_obstacle`, without a comment; no SDK example or community code found reads it). The code assumes metres and the index order `RANGE_OBSTACLE_ORDER` = front, back, left, right, a convention, not a sourced fact. The firmware may fill it only with the robot's own obstacle avoidance on, and may report 0 or a large value for "nothing seen". Checklist item 10 settles all of this; until then `obstacle_m` must not be trusted without `range_obstacle_raw` beside it.

State is **logged only** in v1. No step gets a verdict from it; the model sees only the derived posture. The samples are the data from which v2 verification thresholds will be set ([roadmap.md](../../docs/roadmap.md#v2-plan)).

## Posture rule

`derive_posture(body_height, mode)` in `skills/real.py` is the only place posture is derived from a state sample; the stub stores posture directly in its state file. `body_height` below `POSTURE_SITTING_MAX_M` is `sitting`, at or above `POSTURE_STANDING_MIN_M` is `standing`; in between or `null` is `unknown`.

`mode` is passed in but not used yet, so the rule can switch to `mode` after the checklist without touching any caller. Both thresholds are *tunable* and *unverified*; checklist items 1 and 7 decide them. If neither `body_height` nor `mode` separates sitting from standing, joint angles from `rt/lowstate` (motor `q`) are the fallback source (*unverified*). How posture is carried through a task and shown to the model: [loop-and-context.md](../../dispatcher/docs/loop-and-context.md#user-message).

## Settle waits

Because an SDK action call returns when the command is accepted, a skill waits after a **successful** call so that `state_after` is sampled with the robot at rest:

`sit` after `StandDown()`, `stretch` after `Stretch()` and `stop_move` after `StopMove()`, each for `SETTLE_S` in its module.

The waits are guesses, *tunable* and *unverified*; checklist items 3 and 7 measure them. They are included in `timing.exec_ms` and must fit inside the skill's timeout ([skills.md](skills.md#policies)).

## Open robot-side questions

The list of open questions with owners is in [roadmap.md](../../docs/roadmap.md#open-questions) and [pending human work](../../docs/roadmap.md#pending-human-work). What they mean on the robot side, and what v1 does meanwhile:

- **Odometry.** The robot provides odometry relative to the pose at power-on, which is its origin. `SportModeState` has `position` and `velocity`, and Go2 topic lists include `rt/utlidar/robot_odom` and `rt/utlidar/robot_pose`; which of these carries the estimate, and its accuracy and drift, are *unverified* (checklist item 2). v1 logs `position` and `velocity` in every sample and uses neither. `walk` verification in v2 would use the position delta between `state_before` and `state_after`, so the power-on origin does not matter for it ([roadmap.md](../../docs/roadmap.md#verifiability-per-skill)). A geofence would need a recorded start pose ([roadmap.md](../../docs/roadmap.md#future-ideas)).
- **No skill can stand the robot up.** After `sit`, motion fails until a person stands the robot up with the remote; in a batch run one `sit` affects every later task. On the stub, `go2 --reset-stub` restores the posture. A `stand` skill (`StandUp()` then `BalanceStand()`, with a settle wait) is recommended before experiments ([skills.md](skills.md#adding-a-new-skill)).
- **`Move` while sitting.** v1 treats a non-zero return code as `sdk_error` (the stub returns 1). If the real robot silently ignores `Move` and returns 0, `walk` and `turn` report `ok` while nothing moved. Checklist item 8 finds out.
- **Kill-to-stop latency** and whether the sport service stops by itself when `Move` commands stop arriving: [safety.md](../../docs/safety.md#between-kill-and-stopmove). Checklist item 5 measures the latency.
- **The Python patch version to pin** for the real backend (the lab machine runs 3.10.12): [setup.md](../../docs/setup.md#lab-machine-real-robot).

## Design decisions

- **Posture is derived from sampled state, never inferred from the last skill.** A skill can fail, be killed, or be followed by a manual stand-up with the remote; only the robot's state says what it is doing now. One function holds the rule, so it changes in one place.
- **Fixed settle waits after `StandDown` and `Stretch`**, rather than waiting for the state to settle: simple and inspectable; the values are tuned from the checklist.
- **The network interface is never hardcoded.** A blank `robot.network_interface` with `robot.backend = "real"` is a config error, so the backend never guesses an interface.
- **A fixed lead time per motion skill compensates the start ramp.** In a supervised run, six `turn 90°` (1.6 s commanded) covered 72.6–81.0° by the robot's own `imu_rpy` yaw (mean ~78°, ~0.2 s short at 1 rad/s), and three `walk forward 1 m` (3.3 s commanded) covered 0.59–0.72 m by `position` (mean ~0.67 m, ~1.1 s short at 0.3 m/s); coasting after `StopMove` adds only 1–3° or 0.01 m. The cause is the acceleration ramp at the start of each `Move` sequence, so `LEAD_S` (walk 1.1 s, turn 0.2 s) is added to the commanded time. The budget's motion cost stays the commanded distance or angle; `duration_s` reports the longer time. A constant offset was chosen over a gain only as a single-parameter start: all data is at 90° and 1 m, so the two cannot be separated yet ([roadmap](../../docs/roadmap.md#open-questions)). Rejected for now: closed-loop control on yaw or position, pending the odometry accuracy check (checklist item 2).
- **Obstacle distances come from `SportModeState.range_obstacle`, not from the LiDAR point cloud.** The field arrives on the topic every sample already reads, so it needs no extra subscription and adds no latency or point-cloud processing to a skill process. If firmware leaves it empty, distances per sector from `rt/utlidar/cloud` are the fallback. The raw 4 values are kept next to the derived `obstacle_m` because order, units and the "nothing seen" value are assumptions: the raw log lets a supervised run check them, and lets past runs be re-derived after a correction. Rejected: logging only the derived field (an assumption error would be unrecoverable); logging only the raw array (every consumer would repeat the mapping).
- **Translation is not estimated from commanded velocity.** Dead reckoning restates the walk skill's own open-loop assumption, so it is not a measurement. The robot's own measurement-based estimator is acceptable, with a drift tolerance, if it turns out usable. The stub's pose does integrate commanded velocity, but only as a simulation of that estimator for offline runs ([skills.md](skills.md#stub-backend)).

## Supervised robot checklist

Run this on the lab machine with `robot.backend = "real"`, **in this order**, with the robot **standing**, the Ethernet link to the robot secured, the area clear and the remote (e-stop) in hand. Read [safety.md](../../docs/safety.md) first.

Commands use `go2` with a real-backend config. Run a step as a one-step task, or run the skill by hand ([skills.md](skills.md#running-a-skill-by-hand)), whichever is easier to observe. The run log has every state sample and timing ([run-log.md](../../dispatcher/docs/run-log.md)). State samples do not include `rt/lowstate`, `rt/utlidar/robot_odom` or `rt/utlidar/robot_pose`; read those with a separate DDS subscriber.

1. `go2 state` returns within about 1 s. Record `mode`, `body_height` and `position` while standing. Also record the joint angles (motor `q`) from `rt/lowstate` (fallback posture source).
2. `walk` forward a measured 1 m; `turn` left 90°. Measure the walked distance on the floor (ground truth) and check the angle visually. Record `position` and `imu_rpy` from `state_before` / `state_after` in the run log, and `rt/utlidar/robot_odom` and `rt/utlidar/robot_pose` before and after the walk. Compare each position delta with ground truth (decides the odometry source, accuracy and drift).
3. `stretch` from standing. Measure how long the routine takes (tunes `SETTLE_S` in `stretch.py`).
4. `detect_object` with target `person`, with a person in view. Check position and closeness against what you see.
5. Operator `stop` during a 3 m walk. **Measure** kill-to-stop latency: `stop_move.response.timing.stop_call_ms` from the run log, plus the observed time. Record it; there is no pass threshold.
6. Step timeout during a walk: temporarily set the module constants `MOTION_BASE_S = 0` and `MOTION_FACTOR = 0.5` in `skills/motion.py` (they apply to `turn` too). The robot must stop and the step outcome must be `timeout`. Restore the values afterwards.
7. `sit`. Measure how long `StandDown` takes (tunes `SETTLE_S` in `sit.py`). Then `go2 state`: record `mode` and `body_height` while sitting, and the joint angles (motor `q`) from `rt/lowstate` (decides the [posture rule](#posture-rule)).
8. `walk` while sitting. Record the SDK return code and what the robot does (answers `Move` while sitting).
9. Stand the robot up with the remote.
10. Obstacle distances (needs the robot standing again). Place a wall or a large board at measured distances of 0.5, 1.0 and 2.0 m from the body centre, in turn in front, behind, left and right. At each placement run `go2 state` and record `range_obstacle_raw` and `obstacle_m`, once with the robot's own obstacle avoidance on and once off (remote or app). Decides whether firmware fills the field, its units, its index order (`RANGE_OBSTACLE_ORDER`), its "nothing seen" value and whether it is measured from the body centre or the body edge.

### Results

Record each result here: date, measured value or observation, and what was changed because of it. Then move the resulting fact into the section of this page it belongs to (or the owning document), update the constant in code if a value changed, and remove the resolved item from [roadmap.md](../../docs/roadmap.md).

| # | Date | Result / value | Follow-up |
|---|---|---|---|
| 1 | 2026-10-29 | Standing: `body_height` 0.297–0.324 m, `mode` 0, over 109 state samples; all derived `standing`. `go2 state` time, `position` and motor `q` not recorded. | Sitting values (item 7) still needed before the thresholds can be set. |
| 2 | | | |
| 3 | | | |
| 4 | | | |
| 5 | 2026-10-29 | Successful `stop_move` (3 of 8 calls; 1 operator stop, 2 step timeouts): `stop_call_ms` 891–900 ms, `total_ms` 1404–1428 ms (includes the 0.5 s settle wait and the state sample). Observed time not recorded. | The other 5 calls failed from link loss ([known issues](#known-issues)). |
| 6 | | | |
| 7 | | | |
| 8 | | | |
| 9 | | | |
| 10 | | | |

### Known issues

Causes of the failures in the supervised runs of 2026-10-29. None is a defect in the stop design.

- **Link loss.** The Ethernet cable between the lab machine and the robot was unstable and disconnected. This caused every `Exception: channel factory init error.` (`stop_move` 5 times, `walk` once, `detect_object` twice), every `StopMove returned 3104` at the end of a `turn` (`sdk_error`) and every `GetImageSample returned 3104` (`camera_unavailable`).
- **`turn` 90° killed by its step timeout at 12.36 s** (3 times). The same link loss lost the `StopMove` reply, and the old 10 s `SPORT_CLIENT_TIMEOUT_S` waited past the step timeout. Fixed since: the reply timeout is now 5 s and fits inside every sport-client process timeout, so a lost reply ends as `sdk_error`, not a kill ([skills.md](skills.md#policies)).
- **Every skill needs the robot link, `stop_move` included.** With the link down no software stop reaches the robot; the physical e-stop is the stop ([safety.md](../../docs/safety.md)).
