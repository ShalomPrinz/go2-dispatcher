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

All SDK use is in `skills/real.py` and, for the camera, `skills/real_detector.py` (run only by the [vision service](skills.md#vision-service)), selected by `GO2_BACKEND=real` in the process that uses it: the [robot service](skills.md#robot-service), and the utilities `stop_move` and `read_state` through `skills/local.py`. Skill processes never touch DDS; an import-linter contract forbids it ([skills.md](skills.md#no-side-effects-on-import)). The dispatcher itself never imports the SDK ([architecture.md](../../docs/architecture.md#process-model)); every SDK import is inside a function, so importing any `skills` module needs only the standard library ([skills.md](skills.md#no-side-effects-on-import)).

| What | How |
|---|---|
| DDS init | `ChannelFactoryInitialize(0, $GO2_IFACE)`, once per process, before anything touches DDS: `real.init_dds()`, called by `real.subscribe_state()` and `real.get_sport_client()`: in the robot service once at its start, and in a utility (`local.init_dds()`) before its own sample and sport client. A utility reports its time, including the import of the SDK channel module and failed attempts, as `timing.dds_init_ms` ([skills.md](skills.md#response-schema)). An empty `GO2_IFACE` gives `backend_not_configured`. |
| Sport client | `real.get_sport_client()`: `SportClient()`, `SetTimeout(SPORT_CLIENT_TIMEOUT_S)` (5.0 s, tunable, `skills/robot_ipc.py`), `Init()`. One in the robot service, created at its start and used for every primitive and every stop; one in each `stop_move` process, whose creation it reports as `timing.client_init_ms`. A call that waits for a reply (`StopMove`, `StandDown`, `Stretch`) returns a non-zero code after this long without one; it must fit inside the skill's timeout ([skills.md](skills.md#policies)). Replies are matched per request id and `Move` never waits for one, so the service's `stop` is not queued behind a running primitive. |
| Calls used | `Move(vx, vy, vyaw)`, `StopMove()`, `StandDown()`, `Stretch()`. Each returns an int; 0 is success, anything else is an `sdk_error` with the code in `observations.sdk_ret`. A call that raises in the service is `sdk_exception`, after which the service sends `StopMove`. |
| Camera | `VideoClient`, `SetTimeout(CAMERA_TIMEOUT_S)`, `Init()` once in the vision service, then `GetImageSample()` → JPEG bytes per request, decoded with OpenCV. Error 3104 and any other non-zero code is `camera_unavailable`, never retried. |
| State | `ChannelSubscriber("rt/sportmodestate", SportModeState_)`, queue length `SUBSCRIBER_QUEUE_LEN`, created by `real.subscribe_state(callback)`: once in the robot service, and in each `read_state` or `stop_move` process ([state sampling](#state-sampling)). |

Constants are module-level in `skills/real.py`, except the detector ones ([object detection](#object-detection)) and `SPORT_CLIENT_TIMEOUT_S`. The robot and vision services initialise DDS once per service run; each utility process initialises it once per call ([skills.md](skills.md#design-decisions)).

### Motion

`walk` is open loop: it asks the [robot service](skills.md#robot-service) for one `move` primitive at `VELOCITY_MPS` (`walk.py`) for the travel time plus a fixed lead time `LEAD_S` (1.1 s, tunable) that makes up for the start ramp of each `Move` sequence. The service's motion loop sends `Move` every `CMD_PERIOD_S` (0.1 s, `robot_ipc.py`) for that time, then `StopMove()`. Nothing measures the distance covered.

`turn` is closed loop on the robot's `imu_rpy` yaw: its `move` primitive carries a yaw target, and the service's loop sends `Move` at `YAW_RATE_RPS` (1.0 rad/s, `turn.py`) and `StopMove()` after the period in which the measured yaw change reaches the angle minus `YAW_STOP_MARGIN_RAD` (3°, tunable, `robot_service.py`) for the coast. The cap is `angle / YAW_RATE_RPS × CAP_FACTOR + CAP_S` (1.25 and 0.5 s, tunable; 2.46 s at 90°). When the latest state is missing or older than `MAX_STATE_AGE_S` (0.3 s, tunable), the turn falls back to open loop: from the start for `angle / YAW_RATE_RPS + LEAD_S` (`LEAD_S` 0.2 s, turn's lead time, used only here), mid-turn for the remaining angle at `YAW_RATE_RPS` ([skills.md](skills.md#robot-service)). The closed-loop turn, its margin, cap and age limit are *unverified* on the robot (checklist item 2).

If `Move` returns non-zero, the loop sends `StopMove()` and the skill reports `sdk_error`.

`sit` is `StandDown()` (as in the predecessor skill, not `Sit()`) and `stretch` is the robot's built-in `Stretch()` routine, each an `action` primitive in the service followed by a settle wait in the skill.

**StopMove.** The service sends `StopMove()` through its sport client at the end of every `move`, and whenever motion must end: the dispatcher's `stop` op, the lease's deadline, EOF on the skill's connection, EOF on the dispatcher's control connection, the orphan watchdog, `SIGTERM` and an SDK exception ([lease](skills.md#lease)). A lock serialises every `Move` with the abort of its primitive, so no `Move` is sent after the abort; the `StopMove` is sent from the thread that ended the motion. Its code is reported to the dispatcher in the `stop_sent` reply for its own `stop`, and otherwise in the `disarm` reply's `stop_failed` when the lease's latest `StopMove` failed ([robot service](skills.md#robot-service)). When the service is hung or dead, the dispatcher runs the fallback `stop_move` utility, a fresh process with its own DDS participant and sport client ([safety.md](../../docs/safety.md#the-stop-path)).

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

- at the start and end of every skill call (`state_before`, `state_after`), through the [robot service](skills.md#robot-service);
- by the `stop_move` utility after `StopMove()` and its [settle wait](#settle-waits) (`state_after`), with its own DDS;
- by the `read_state` utility, with its own DDS: `go2 state`, and at startup on either backend to set the dispatcher's initial state, posture and obstacle distances included (no state, posture `unknown`, if it fails).

**How.** One `state_store.StateStore` per subscriber holds the latest `rt/sportmodestate` message. The subscriber callback (`StateStore.put`) only stores the message, numbers it `seq` from 1 and stamps its arrival time; the message is mapped to a `RobotState` when it is read. In the robot service the subscription lives for the whole service run; in a utility it is made at the first sample. A sample waits up to `STATE_WAIT_S` (1.0 s) for a message that arrived *after* the sample was requested (`min_seq` = latest `seq` + 1); if none arrives, it uses the latest earlier message; if there is none at all, the sample fails. A failed sample does not fail the step: it is reported in `state_error` and the state is `null` ([skills.md](skills.md#response-schema)). The robot service also reports the link: `up` while the latest message is at most `LINK_GAP_S` (0.5 s, *tunable*) old, else `down`.

**Fields.** Each sample is a `RobotState` (`skills/schema.py`), which rejects unknown keys, so a new field is added there first ([skills.md](skills.md#response-schema)):

| Field | Source in `SportModeState` |
|---|---|
| `t` | local `time.time()` when the sample was read |
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
| `seq` | the message's number, counted from 1 by the subscriber that received it (the robot service, or the utility's own); not a field of the message |
| `age_ms` | time from the message's arrival at the subscriber to the read |
| `obstacle_m` | `{front, back, left, right}` in metres, derived from `range_obstacle_raw` by `derive_obstacles` in `skills/real.py`; a value of 0 or below, above `RANGE_OBSTACLE_MAX_M` (30 m, *tunable*) or `null` becomes `null` (unknown) |

numpy and ctypes values are converted to plain numbers. A missing attribute or a non-finite value becomes `null`; a list field other than `range_obstacle_raw` becomes `null` as a whole if any element is bad or its length is wrong.

**Obstacle distances are *unverified*.** Unitree documents neither the units, the index order nor when firmware fills `range_obstacle` (the SDK IDL and `unitree_ros2`'s `SportModeState.msg` declare only `float32[4] range_obstacle`, without a comment; no SDK example or community code found reads it). The code assumes metres and the index order `RANGE_OBSTACLE_ORDER` = front, back, left, right, a convention, not a sourced fact. The firmware may fill it only with the robot's own obstacle avoidance on, and may report 0 or a large value for "nothing seen". Checklist item 10 settles all of this; until then the model uses `obstacle_m` alone, on the assumption that the field works, and `range_obstacle_raw` stays log-only so a supervised run can verify it.

State is **logged only** in v1. No step gets a verdict from it; the model sees only the derived posture and `obstacle_m` ([loop-and-context.md](../../dispatcher/docs/loop-and-context.md#user-message)). The samples are the data from which v2 verification thresholds will be set ([roadmap.md](../../docs/roadmap.md#v2-plan)).

### Working assumptions

The robot service rests on these assumptions, none yet checked on the robot (checklist items 11 to 13). If a supervised run contradicts one, the design consequence is revisited.

- **`Move` commands stop arriving.** Assumed, as the worst case: the Go2 keeps its last velocity until a `StopMove` arrives. Consequence: every loss path (lease connection closed, deadline, control connection closed, dispatcher death, service death) ends in an explicit `StopMove`, warm from the service or from the fallback `stop_move`; between a service crash and the fallback stop (about 1 s) the robot may keep moving.

- **Cable unplug and replug.** Assumed: a long-lived CycloneDDS participant recovers by itself once the interface is back with its address. Consequence: the service has no logic to re-create its participant or subscription. A link that does not recover shows as `link` `down` and growing `age_ms` in every read, and as failed SDK calls in the skills; the operator restarts the dispatcher.
- **`rt/sportmodestate` rate.** Assumed: at least 50 Hz. Consequence: the latest message is always recent enough for a state read; the `min_seq` wait is short, and `age_ms` in every sample shows the real freshness. The sim source publishes at the same 50 Hz.
- **Callback cost.** Assumed: storing the latest message holds the GIL for microseconds, so the DDS callback and the service's connection threads do not delay each other measurably. Consequence: one service process with plain threads, no separate process or thread priorities.
- **Warm `StopMove` round trip.** Assumed: tens of milliseconds from the service's `stop` handler to the robot's reply. Consequence: the dispatcher's wait for `stop_sent` is 500 ms (tunable), well above it, so a missed reply means a hung service, not a slow robot. Checked by `stop_call_ms` in the `stop_sent` reply and the dispatcher's kill-to-stop time in run logs (checklist item 12).
- **Fallback beside a hung service.** Assumed: the fallback `stop_move` process can send `StopMove` through its own participant and sport client while a hung service still holds its sport client. Consequence: no handover between them; the dispatcher kills the service and runs the fallback without waiting for the service to release anything (checklist item 13).
- **10 Hz loop jitter.** Assumed: under 20 ms, with the state callback only storing the latest message. Consequence: the motion loop is a plain thread in the service, with no separate process or thread priority. Not measured; uneven walks or turns on the robot would be the signal to log `Move` send times.

## Posture rule

`derive_posture(body_height, mode)` in `skills/real.py` is the only place posture is derived from a state sample; the stub stores posture directly in its state file. `body_height` below `POSTURE_SITTING_MAX_M` is `sitting`, at or above `POSTURE_STANDING_MIN_M` is `standing`; in between or `null` is `unknown`.

`mode` is passed in but not used yet, so the rule can switch to `mode` after the checklist without touching any caller. Both thresholds are *tunable* and *unverified*; checklist items 1 and 7 decide them. If neither `body_height` nor `mode` separates sitting from standing, joint angles from `rt/lowstate` (motor `q`) are the fallback source (*unverified*). How posture and obstacle distances are carried through a task and shown to the model: [loop-and-context.md](../../dispatcher/docs/loop-and-context.md#user-message).

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
- **A fixed lead time compensates the start ramp of open-loop motion.** In a supervised run, six `turn 90°` (1.6 s commanded) covered 72.6–81.0° by the robot's own `imu_rpy` yaw (mean ~78°, ~0.2 s short at 1 rad/s), and three `walk forward 1 m` (3.3 s commanded) covered 0.59–0.72 m by `position` (mean ~0.67 m, ~1.1 s short at 0.3 m/s); coasting after `StopMove` adds only 1–3° or 0.01 m. The cause is the acceleration ramp at the start of each `Move` sequence, so `LEAD_S` (walk 1.1 s, turn 0.2 s) is added to the commanded time of `walk` and of turn's open-loop fallback. The budget's motion cost stays the commanded distance or angle; `duration_s` reports the longer time. A constant offset was chosen over a gain only as a single-parameter start: all data is at 90° and 1 m, so the two cannot be separated yet ([roadmap](../../docs/roadmap.md#open-questions)). Rejected for now: closed-loop control on position, pending the odometry source and accuracy check (checklist item 2).
- **`turn` is closed loop on `imu_rpy` yaw, stopping early by a fixed margin.** The yaw read by the same `rt/sportmodestate` message measured the shortfall above, so it can end the turn at any angle instead of a lead time fitted at 90°; the 3° margin covers the measured 1–3° coast. The robot's start ramp lengthens the turn, so the cap (`CAP_FACTOR`, `CAP_S`) allows about 0.9 s more than the nominal time at 90°. The control law, the shortfall policy and the open-loop fallback on stale yaw, with their rejected alternatives, are in [skills.md](skills.md#design-decisions).
- **Obstacle distances come from `SportModeState.range_obstacle`, not from the LiDAR point cloud.** The field arrives on the topic every sample already reads, so it needs no extra subscription and adds no latency or point-cloud processing to a skill process. If firmware leaves it empty, distances per sector from `rt/utlidar/cloud` are the fallback. The raw 4 values are kept next to the derived `obstacle_m` because order, units and the "nothing seen" value are assumptions: the raw log lets a supervised run check them, and lets past runs be re-derived after a correction. Rejected: logging only the derived field (an assumption error would be unrecoverable); logging only the raw array (every consumer would repeat the mapping).
- **Translation is not estimated from commanded velocity.** Dead reckoning restates the walk skill's own open-loop assumption, so it is not a measurement. The robot's own measurement-based estimator is acceptable, with a drift tolerance, if it turns out usable. The stub's pose does integrate commanded velocity, but only as a simulation of that estimator for offline runs ([skills.md](skills.md#stub-backend)).

## Supervised robot checklist

Run this on the lab machine with `robot.backend = "real"`, **in this order**, with the robot **standing**, the Ethernet link to the robot secured, the area clear and the remote (e-stop) in hand. Read [safety.md](../../docs/safety.md) first.

Commands use `go2` with a real-backend config. Run a step as a one-step task, or run the skill by hand ([skills.md](skills.md#running-a-skill-by-hand)), whichever is easier to observe. The run log has every state sample and timing ([run-log.md](../../dispatcher/docs/run-log.md)). State samples do not include `rt/lowstate`, `rt/utlidar/robot_odom` or `rt/utlidar/robot_pose`; read those with a separate DDS subscriber.

1. `go2 state` returns within about 1 s. Record `mode`, `body_height` and `position` while standing. Also record the joint angles (motor `q`) from `rt/lowstate` (fallback posture source).
2. `walk` forward a measured 1 m; `turn` left 90°. Measure the walked distance on the floor (ground truth) and check the angle visually. Record `position` and `imu_rpy` from `state_before` / `state_after` in the run log, and `rt/utlidar/robot_odom` and `rt/utlidar/robot_pose` before and after the walk. Compare each position delta with ground truth (decides the odometry source, accuracy and drift).
3. `stretch` from standing. Measure how long the routine takes (tunes `SETTLE_S` in `stretch.py`).
4. `detect_object` with target `person`, with a person in view. Check position and closeness against what you see.
5. Operator `stop` during a 3 m walk. **Measure** kill-to-stop latency from the run log ([safety.md](../../docs/safety.md#measured-latency)), plus the observed time. Record it; there is no pass threshold.
6. Step timeout during a walk: temporarily set the module constants `MOTION_BASE_S = 0` and `MOTION_FACTOR = 0.5` in `skills/motion.py` (they apply to `turn` too). The robot must stop and the step outcome must be `timeout`. Restore the values afterwards.
7. `sit`. Measure how long `StandDown` takes (tunes `SETTLE_S` in `sit.py`). Then `go2 state`: record `mode` and `body_height` while sitting, and the joint angles (motor `q`) from `rt/lowstate` (decides the [posture rule](#posture-rule)).
8. `walk` while sitting. Record the SDK return code and what the robot does (answers `Move` while sitting).
9. Stand the robot up with the remote.
10. Obstacle distances (needs the robot standing again). Place a wall or a large board at measured distances of 0.5, 1.0 and 2.0 m from the body centre, in turn in front, behind, left and right. At each placement run `go2 state` and record `range_obstacle_raw` and `obstacle_m`, once with the robot's own obstacle avoidance on and once off (remote or app). Decides whether firmware fills the field, its units, its index order (`RANGE_OBSTACLE_ORDER`), its "nothing seen" value and whether it is measured from the body centre or the body edge.

11. Robot service (needs the robot standing). With the dispatcher running, read the state 20 times in a row through the service and record `seq` and `age_ms`: the `seq` increase per second is the message rate (assumed ≥ 50 Hz), and `age_ms` should stay below 20 ms. Then unplug the Ethernet cable for about 5 s and plug it back in, without restarting anything: record whether reads show `link` `down` during the gap and `up` with growing `seq` afterwards (assumed: the participant recovers by itself). See [working assumptions](#working-assumptions).
12. Warm-stop latency (needs the robot standing). Operator `stop` during a 3 m walk, five times. Record `stop_call_ms` from the service's `stop_sent` reply and the dispatcher's kill-to-stop time from the run log, and check that the stop went through the service, not the fallback. Assumed: tens of ms, well under the 500 ms `stop_sent` wait. Also time from the video or by eye when the robot stops moving.
13. Fallback stop beside a hung service (needs the robot standing). Start a 3 m walk by hand under a lease (`python -m skills.lease`, [skills.md](skills.md#running-a-skill-by-hand)); after about 1 s run `kill -STOP <service pid>`, then `python -m skills.stop_move '{}'`. The robot must stop; record `stop_call_ms` and `sdk_ret` from its response. Then `kill -9` the service. Assumed: the fallback works while the hung service holds its sport client.

### Results

Record each result here: date, measured value or observation, and what was changed because of it. Then move the resulting fact into the section of this page it belongs to (or the owning document), update the constant in code if a value changed, and remove the resolved item from [roadmap.md](../../docs/roadmap.md).

| # | Date | Result / value | Follow-up |
|---|---|---|---|
| 1 | 2026-10-29 | Standing: `body_height` 0.297–0.324 m, `mode` 0, over 109 state samples; all derived `standing`. `go2 state` time, `position` and motor `q` not recorded. | Sitting values (item 7) still needed before the thresholds can be set. |
| 2 | | | |
| 3 | | | |
| 4 | | | |
| 5 | 2026-10-29 | Successful `stop_move` (3 of 8 calls; 1 operator stop, 2 step timeouts): `stop_call_ms` 891–900 ms, from utility start, so DDS init and client creation included ([reading records without `dds_init_ms`](skills.md#response-schema)), `total_ms` 1404–1428 ms (includes the 0.5 s settle wait and the state sample). Observed time not recorded. | The other 5 calls failed from link loss ([known issues](#known-issues)). |
| 6 | | | |
| 7 | | | |
| 8 | | | |
| 9 | | | |
| 10 | | | |
| 11 | | | |

### Known issues

Causes of the failures in the supervised runs of 2026-10-29. None is a defect in the stop design.

- **Link loss.** The Ethernet cable between the lab machine and the robot was unstable and disconnected. This caused every `Exception: channel factory init error.` (`stop_move` 5 times, `walk` once, `detect_object` twice), every `StopMove returned 3104` at the end of a `turn` (`sdk_error`) and every `GetImageSample returned 3104` (`camera_unavailable`).
- **`turn` 90° killed by its step timeout at 12.36 s** (3 times). The same link loss lost the `StopMove` reply, and the old 10 s `SPORT_CLIENT_TIMEOUT_S` waited past the step timeout. Fixed since: the reply timeout is now 5 s and fits inside every sport-client process timeout, so a lost reply ends as `sdk_error`, not a kill ([skills.md](skills.md#policies)).
- **Every skill needs the robot link, `stop_move` included.** With the link down no software stop reaches the robot; the physical e-stop is the stop ([safety.md](../../docs/safety.md)).
