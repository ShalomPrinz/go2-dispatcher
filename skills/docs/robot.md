# The robot

The Go2 EDU side of the system: what the code assumes about the robot, how it talks to it through the Unitree SDK over DDS, how state is sampled and turned into a posture, and the supervised checklist that verifies all of this on the robot. The skill contract (invocation, response schema, error codes, policies, stub backend) is in [skills.md](skills.md); stopping the robot is in [safety.md](../../docs/safety.md); installing the SDK is in [setup.md](../../docs/setup.md).

**Status: the real backend has never been run on the robot.** Everything on this page that depends on the robot's behaviour is *unverified* until the [checklist](#supervised-robot-checklist) has been worked through.

## Robot facts

- **Unitree Go2 EDU**, controlled directly through the Unitree SDK2 Python bindings (`unitree_sdk2py`) over CycloneDDS. No ROS.
- Reached over a wired Ethernet interface, on the robot's subnet (usually `192.168.123.x`; the robot is `192.168.123.161`). The interface name is a config value (`robot.network_interface`), never hardcoded ([setup.md](../../docs/setup.md#2-network-interface)).
- DDS domain 0.
- The IMU and sport mode are assumed available through `SportModeState` on topic `rt/sportmodestate` (*unverified*).
- **SDK action calls return when the command is accepted, not when the motion ends.** This is why `sit` and `stretch` wait after their call ([settle waits](#settle-waits)).
- The robot's low-level contact person is Achiya ([project.md](../../docs/project.md)); ask him about SDK topics, state fields and odometry.

## SDK and DDS usage

All SDK use is in `skills/real.py`, selected in the skill process by `GO2_BACKEND=real` (`backend.py`). The dispatcher itself never imports the SDK ([architecture.md](../../docs/architecture.md#process-model)); every SDK import is inside a function, so importing any `skills` module needs only the standard library ([skills.md](skills.md#no-side-effects-on-import)).

| What | How |
|---|---|
| DDS init | `ChannelFactoryInitialize(0, $GO2_IFACE)`, once per process, before anything touches DDS. An empty `GO2_IFACE` gives `backend_not_configured`. |
| Sport client | `SportClient()`, `SetTimeout(SPORT_CLIENT_TIMEOUT_S)`, `Init()`. One per skill process. |
| Calls used | `Move(vx, vy, vyaw)`, `StopMove()`, `StandDown()`, `Stretch()`. Each returns an int; 0 is success, anything else is an `sdk_error` with the code in `observations.sdk_ret`. |
| Camera | `VideoClient`, `SetTimeout(CAMERA_TIMEOUT_S)`, `Init()`, `GetImageSample()` → JPEG bytes, decoded with OpenCV. |
| State | `ChannelSubscriber("rt/sportmodestate", SportModeState_)`, queue length `SUBSCRIBER_QUEUE_LEN` ([state sampling](#state-sampling)). |

Constants are module-level in `skills/real.py` (detector ones on `RealDetector`). Each skill call and utility is a fresh process, so DDS is initialised once per call ([skills.md](skills.md#design-decisions)).

### Motion

`walk` and `turn` are open-loop: they send `Move` every `CMD_PERIOD_S` at `VELOCITY_MPS` (both in `walk.py`) or `YAW_RATE_RPS` (`CMD_PERIOD_S` and `YAW_RATE_RPS` in `turn.py`) for the time the motion should take, then `StopMove()`. Nothing measures the distance or angle covered. If `Move` returns non-zero, the loop sends `StopMove()` and reports `sdk_error`. If the dispatcher dies, the loop notices on its next iteration, stops the robot and exits ([safety.md](../../docs/safety.md#if-the-dispatcher-dies)).

`sit` is `StandDown()` and `stretch` is the robot's built-in `Stretch()` routine, each followed by a settle wait.

### Object detection

`detect_object` on the real backend takes one frame from the front camera and runs YOLO on it (`ultralytics`, weights from `robot.yolo_weights`):

- The weights file is checked before the camera is touched and is **never downloaded automatically**; a missing file is `weights_missing`. YOLO is loaded on the first detection in the process.
- Inference at `IMGSZ` with confidence threshold `CONF`. Of the boxes whose class equals the target, the one with the highest confidence is used.
- **Position** from the box centre's horizontal fraction of the image (`LEFT_MAX_X`, `RIGHT_MIN_X`); **closeness** from the box height's fraction (`NEAR_MIN_H`, `MEDIUM_MIN_H`).
- Confidence is rounded to `CONFIDENCE_DECIMALS` decimals.

All thresholds are class constants of `RealDetector`, *tunable* and *unverified* on the robot's camera.

## State sampling

Robot state is sampled:

- at the start and end of every skill call (`state_before`, `state_after`);
- by the `stop_move` utility after `StopMove()` and its [settle wait](#settle-waits) (`state_after`);
- by the `read_state` utility: `go2 state`, and at startup on the real backend to set the initial posture (`unknown` if it fails).

**How.** On the first sample in a process, the backend subscribes to `rt/sportmodestate`. A sample waits up to `STATE_WAIT_S` for a message that arrived *after* the sample was requested; if none arrives, it uses the latest earlier message; if there is none at all, the sample fails. A failed sample does not fail the step: it is reported in `state_error` and the state is `null` ([skills.md](skills.md#response-schema)).

**Fields.** Each sample is a `RobotState`:

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

numpy and ctypes values are converted to plain numbers. A missing attribute or a non-finite value becomes `null`; a list field becomes `null` as a whole if any element is bad or its length is wrong.

State is **logged only** in v1. No step gets a verdict from it; the model sees only the derived posture. The samples are the data from which v2 verification thresholds will be set ([roadmap.md](../../docs/roadmap.md#v2-plan)).

## Posture rule

`derive_posture(body_height, mode)` in `skills/real.py` is the only place posture is derived from a state sample; the stub stores posture directly in its state file. `body_height` below `POSTURE_SITTING_MAX_M` is `sitting`, at or above `POSTURE_STANDING_MIN_M` is `standing`; in between or `null` is `unknown`.

`mode` is passed in but not used yet, so the rule can switch to `mode` after the checklist without touching any caller. Both thresholds are *tunable* and *unverified*; checklist items 1 and 7 decide them. How posture is carried through a task and shown to the model: [loop-and-context.md](../../dispatcher/docs/loop-and-context.md#user-message).

## Settle waits

Because an SDK action call returns when the command is accepted, a skill waits after a **successful** call so that `state_after` is sampled with the robot at rest:

`sit` after `StandDown()`, `stretch` after `Stretch()` and `stop_move` after `StopMove()`, each for `SETTLE_S` in its module.

The waits are guesses, *tunable* and *unverified*; checklist items 3 and 7 measure them. They are included in `timing.exec_ms` and must fit inside the skill's timeout ([skills.md](skills.md#policies)).

## Open robot-side questions

The list of open questions with owners is in [roadmap.md](../../docs/roadmap.md#open-questions) and [pending human work](../../docs/roadmap.md#pending-human-work). What they mean on the robot side, and what v1 does meanwhile:

- **Odometry.** `SportModeState` has `position` and `velocity`, and Go2 topic lists include `rt/utlidar/robot_odom` and `rt/utlidar/robot_pose`. Whether the robot's own estimate is available and usable is unknown (ask Achiya). v1 logs `position` and `velocity` in every sample and uses neither. The answer decides whether `walk` can be verified in v2 and whether a geofence is possible ([roadmap.md](../../docs/roadmap.md#verifiability-per-skill)).
- **No skill can stand the robot up.** After `sit`, motion fails until a person stands the robot up with the remote; in a batch run one `sit` affects every later task. On the stub, `go2 --reset-stub` restores the posture. A `stand` skill (`StandUp()` then `BalanceStand()`, with a settle wait) is recommended before experiments ([skills.md](skills.md#adding-a-new-skill)).
- **`Move` while lying down.** v1 treats a non-zero return code as `sdk_error` (the stub returns 1). If the real robot silently ignores `Move` and returns 0, `walk` and `turn` report `ok` while nothing moved. Checklist item 8 finds out.
- **Kill-to-stop latency** and whether the sport service stops by itself when `Move` commands stop arriving: [safety.md](../../docs/safety.md#between-kill-and-stopmove). Checklist item 5 measures the latency.
- **Installing the `robot` extra on the lab machine** (CycloneDDS build) and the Python version to pin: [setup.md](../../docs/setup.md#lab-machine-real-robot).

## Design decisions

- **Posture is derived from sampled state, never inferred from the last skill.** A skill can fail, be killed, or be followed by a manual stand-up with the remote; only the robot's state says what it is doing now. One function holds the rule, so it changes in one place.
- **Fixed settle waits after `StandDown` and `Stretch`**, rather than waiting for the state to settle: simple and inspectable; the values are tuned from the checklist.
- **The network interface is never hardcoded.** A blank `robot.network_interface` with `robot.backend = "real"` is a config error, so the backend never guesses an interface.
- **Translation is not estimated from commanded velocity.** Dead reckoning restates the walk skill's own open-loop assumption, so it is not a measurement. The robot's own measurement-based estimator is acceptable, with a drift tolerance, if it turns out usable.

## Supervised robot checklist

Run this on the lab machine with `robot.backend = "real"`, **in this order**, with the robot **standing**, the area clear and the remote (e-stop) in hand. Read [safety.md](../../docs/safety.md) first.

Commands use `go2` with a real-backend config. Run a step as a one-step task, or run the skill by hand ([skills.md](skills.md#running-a-skill-by-hand)), whichever is easier to observe. The run log has every state sample and timing ([run-log.md](../../dispatcher/docs/run-log.md)).

1. `go2 state` returns within about 1 s. Record `mode`, `body_height` and `position` while standing.
2. `walk` forward 0.5 m; `turn` left 90°. Check the distance and angle visually. Record `position` and `imu_rpy` from `state_before` / `state_after` in the run log (evidence for the odometry question).
3. `stretch` from standing. Measure how long the routine takes (tunes `SETTLE_S` in `stretch.py`).
4. `detect_object` with target `person`, with a person in view. Check position and closeness against what you see.
5. Operator `stop` during a 3 m walk. **Measure** kill-to-stop latency: `stop_move.response.timing.stop_call_ms` from the run log, plus the observed time. Record it; there is no pass threshold.
6. Step timeout during a walk: temporarily set the module constants `BASE_S = 0` and `FACTOR = 0.5` in `skills/walk.py`. The robot must stop and the step outcome must be `timeout`. Restore the values afterwards.
7. `sit`. Measure how long `StandDown` takes (tunes `SETTLE_S` in `sit.py`). Then `go2 state`: record `mode` and `body_height` while sitting (decides the [posture rule](#posture-rule)).
8. `walk` while sitting. Record the SDK return code and what the robot does (answers `Move` while lying down).
9. Stand the robot up with the remote.

### Results

Record each result here: date, measured value or observation, and what was changed because of it. Then move the resulting fact into the section of this page it belongs to (or the owning document), update the constant in code if a value changed, and remove the resolved item from [roadmap.md](../../docs/roadmap.md).

| # | Date | Result / value | Follow-up |
|---|---|---|---|
| 1 | | | |
| 2 | | | |
| 3 | | | |
| 4 | | | |
| 5 | | | |
| 6 | | | |
| 7 | | | |
| 8 | | | |
| 9 | | | |
