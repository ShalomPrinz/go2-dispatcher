# Roadmap

What comes next. When an item here is resolved, move the result into the owning document (as a fact or a design decision) and delete it from this page.

## v2 plan

Agreed direction with the advisor. The study's experiments run only after v2 is implemented ([project.md](project.md#study-design)).

### Verification layer

Implemented: every dispatched step gets fault detection, a step with outcome `ok` also gets goal verification and a verdict, a `failed` verdict abandons the rest of the plan and calls the model again, and a step whose precondition is not met is not dispatched ([loop-and-context.md](../dispatcher/docs/loop-and-context.md#verification)). Conceptual basis: DoReMi ([references.md](references.md)). Still pending:

- **`walk` verification, dependent on odometry.** The robot provides odometry relative to its power-on pose ([robot.md](../skills/docs/robot.md#open-robot-side-questions)). Which source carries it (`SportModeState.position` on `rt/sportmodestate`, or `rt/utlidar/robot_odom` / `rt/utlidar/robot_pose`) and its accuracy and drift are open ([pending work](#pending-human-work)). If the estimate is accurate enough, `walk` gets a position-delta expectation (with a drift tolerance; the power-on origin does not matter), which would also catch a stuck walk, and a geofence becomes possible. Keep this distinction: the robot's **own measurement-based estimator** is acceptable as a verification source; **dead reckoning from commanded velocity** is not, because it restates the walk skill's own assumption (walk is open-loop: it commands a speed for a time). Owner: dispatcher and skills, after checklist item 2.
- **Fault thresholds on the robot.** `FALLEN_TILT_RAD` and `FAULT_MODES` are assumed signatures (*unverified*); the first fall in a run log, or Unitree's documentation of `mode`, settles them ([robot.md](../skills/docs/robot.md#open-robot-side-questions), checklist item 9).

### `ASK` plan status

A dedicated status for clarifying questions (for example "go to the chair" when there are two chairs). In v1 the model returns `ABORT` with the question in `message`.

### Seams already in v1

Kept on purpose; do not remove them as dead code.

- `state_before` / `state_after` on every skill response and `state_after` on every `StopMove`, including the robot's own `position` estimate when published.
- Typed `expect` and `requires` keys in `SKILL.md` frontmatter, declared for every v1 skill and not rendered into the catalog; the dispatcher evaluates them for verdicts and preconditions ([skills.md](../skills/docs/skills.md#expectations-and-preconditions)).
- A single posture rule, `derive_posture()` in `skills/real.py`.
- Plan status normalised in one place (`Plan._normalise_status` in `models.py`), so adding `ASK` is local.
- Stub robot faults (`fallen`, `stuck`, `rejected`) with the real sample's fields, which the fault-detection tests use ([skills.md](../skills/docs/skills.md#robot-faults)).
- `skills.dir` is configurable, so other granularity tiers can be loaded.

## Future ideas

Not scheduled.

- **Further granularity tiers** (medium, composite) as separate skill folders selected with `skills.dir`. This is the main study's manipulated variable, so it is needed before the participant study.
- **Forward messages received during a run to the LLM.** Decide between letting the model update the plan in place (the dispatcher would need to support replacing a running plan) and limiting it to classifying the message as a stop or replying to the sender with text. In v1 only the stop word is recognised during a task; other messages get a busy reply.
- **Prompt caching** for the static slots (tools, system text, catalog). Reduces cost, but cached tokens are reported separately and cache behaviour differs between skill sets of different sizes, so token comparisons would need uncached-equivalent reporting. The slot order already allows it.
- **Geofence**, if the odometry is accurate enough (see odometry above). At every session start the dispatcher records the start pose, and the fence is defined relative to it. Two limits:
  - The fence is relative to the start pose, not to the room. A fence around fixed obstacles needs the robot started from a known spot.
  - A robot reboot mid-session resets the odometry origin, so the start pose must be recorded again.
- **Continuous monitoring during a skill**: a watchdog that can abort mid-motion rather than only between steps.
- **Independent batch tasks.** `batch` carries the previous task and posture across lines; experiments may need a reset per task (for example an `--independent` flag).
- **Claude Code guard hooks** for the real backend: a fail-closed PreToolUse `Bash` hook blocking `--backend real` / `--run-robot` (permission patterns miss `sh -c` and path prefixes) and an `Edit|Write` hook blocking `backend = "real"` in tracked config ([safety.md](safety.md#design-decisions)).
- **Less thinking than `between_tools`**: if Claude at `between_tools` (already the lowest setting Sonnet 5.5 accepts) measures too slow, the options are a model that accepts thinking disabled, or a lower `max_tokens` cap. Either changes the request, so it needs an API check and a measurement-comparability check ([llm.md](../dispatcher/docs/llm.md)).
- **Start moving before the full plan arrives**: for example, run the first step while the rest of the plan is still being generated, when it is clear from the start that the robot must walk. This would change the loop and the plan-validation contract ([loop-and-context.md](../dispatcher/docs/loop-and-context.md)).
- **Long-term memory across sessions**, for example remembering a person's name. Today everything is per task and session, apart from the run logs; whether this is in the project's scope is not decided.

## Open questions

| Question | Current v1 behaviour | Resolved by |
|---|---|---|
| Participant protocol: number of participants, session procedure, consent and ethics approval, participant safety around the robot beyond the [supervised-operation rules](safety.md#supervised-operation-rules). | Not designed | Experiment design with the advisor |
| Task set: which tasks participants give, and whether each can run on both systems (the OpenClaw-baseline assumption, [project.md](project.md#openclaw-as-a-system-level-baseline)). Wait steps ([candidate tasks](project.md#preparing-the-openclaw-2-comparison)) need a skill on both systems; `skills/catalog` has none today. | Not designed | Experiment design with the advisor |
| How subjective trust and interaction quality are measured (questionnaire or other instrument). Dispatcher load is computed from the run log ([run-log.md](../dispatcher/docs/run-log.md#computing-the-study-metrics)). | Not designed | Experiment design with the advisor |
| How are skill granularity and planning horizon crossed in the experiment? | — | Experiment design with the advisor |
| Does the OpenClaw baseline run under one granularity condition or all of them? | — | Experiment design (task set) |
| `max_llm_calls` per horizon condition. At horizon 1 long tasks may hit the cap, so `CALL_BUDGET_EXHAUSTED` rates partly reflect the cap. | One value for all conditions, logged per task | Experiment design |
| Add a `stand` skill (`StandUp` then `BalanceStand`, with a settle wait)? No skill can stand the robot up: after `sit`, motion fails until a person stands it up, and in a batch run one `sit` affects every later task. | Not added; the model sees `Posture: sitting` and should `ABORT` with an explanation. Adding it is one `SKILL.md` plus one module. Recommended. | Decide before experiments |
| Final wording of the system text, notices and operator messages. It must be identical across conditions, because directive text steers behaviour. | Current texts in `prompts.py` ([loop-and-context.md](../dispatcher/docs/loop-and-context.md)) | Freeze before experiments |
| Is the horizon-rejection rate acceptable, or should long plans be handled differently? | Rejected, never truncated; rate logged ([llm.md](../dispatcher/docs/llm.md)) | Inspect the rate of `plan_invalid` with `rejection_kind == "horizon"` from runs |
| Jev (a "TypeSafe AI" decision model) as an alternative LLM layer? The planner only needs decision making plus quantified parameters (for example "walk 3"). | Under consideration, not decided | Advisor and student |
| Is the walk shortfall a constant lead time or a gain? Every real walk so far is `walk 1 m`, which cannot separate the two. Every walk also veers about 5–10° in heading. | Fixed `LEAD_S` (walk 1.1 s), open loop, no heading correction. Turn stops on the measured yaw; its 0.2 s `LEAD_S` applies only to its open-loop fallback ([robot.md](../skills/docs/robot.md#motion)) | User, with supervised runs at other angles and distances (e.g. 45° and 180°, 0.3 m and 2 m) |
| Does firmware fill `SportModeState.range_obstacle`, and with what units, index order, "nothing seen" value and reference point? Unitree documents none of these. | Assumed metres, order front, back, left, right; logged raw (`range_obstacle_raw`) beside the derived `obstacle_m`, both *unverified* ([robot.md](../skills/docs/robot.md#state-sampling)) | User, with supervised checklist item 10 ([robot.md](../skills/docs/robot.md#supervised-robot-checklist)) |
| Reduce latency to the 4 s target if Sonnet measures above it; prompt caching is the first candidate. | No latency work; `go2 report` measures against the target ([run-log.md](../dispatcher/docs/run-log.md#latency-report-go2-report)) | Student, after the Sonnet measurement |
| Every starting value: budgets, limits, timeouts, parameter ranges, settle waits, posture thresholds. | Marked *tunable* in [configuration.md](configuration.md) and the topic docs | Tune from logs and robot runs |

## Before experiments

The gate for experiments with participants, collected from the sections above. Each item is specified where it is linked.

1. Resolve the [pending human work](#pending-human-work) below (live API check, robot checklist, Python pin, Telegram test, odometry source and accuracy).
2. Implement the [v2 plan](#v2-plan).
3. Build the medium and composite skill tiers ([future ideas](#future-ideas)); decide the `stand` skill and whether `batch` needs a per-task reset.
4. Run phase one: characterise the system in simulation and from run logs ([project.md](project.md#study-design)); tune the starting values.
5. Settle the experiment-design [open questions](#open-questions) with the advisor: protocol, task set, trust measurement, how granularity and horizon are crossed, OpenClaw conditions, `max_llm_calls` per horizon.
6. Freeze the system text, notices and operator messages; from then on the registry hash must stay fixed within a condition ([skills.md](../skills/docs/skills.md#catalog-and-registry-hash)).

## Pending human work

| Item | Owner / how |
|---|---|
| Odometry: which source carries the robot's position estimate (`SportModeState.position`, `rt/utlidar/robot_odom` or `rt/utlidar/robot_pose`), and its accuracy and drift. Decides `walk` verification and the geofence. | Supervised robot checklist item 2 in [robot.md](../skills/docs/robot.md#supervised-robot-checklist) |
| Live API check of the LLM request parameters (Sonnet 5.5, `between_tools`, auto tool choice). | `ANTHROPIC_API_KEY=... uv run pytest --run-live -s tests/integration/test_live_llm.py` ([testing.md](../tests/docs/testing.md#live-llm-test)) |
| Calibrate the detector closeness thresholds (`NEAR_MIN_H`, `MEDIUM_MIN_H` in `skills/vision.py`) on the robot. With the current values a 0.25 m bottle reads `near` only within about 0.2 m of the camera ([robot.md](../skills/docs/robot.md#object-detection)). | Supervised human check: bottle, chair and person at measured distances; record box height fractions in [robot.md](../skills/docs/robot.md) |
| Supervised robot checklist: posture thresholds (`body_height` or `mode`, with `rt/lowstate` joint angles recorded as fallback), settle waits, what `Move` does while sitting, kill-to-stop latency, obstacle distances (`range_obstacle`, item 10). | Supervised run on the robot; record results in [robot.md](../skills/docs/robot.md) |
| `mode` after `sit` (unconfirmed; the stub assumes 5) and after `stretch` at rest (0 expected). Decides whether `sit` also expects a `mode`, and the stub's `MODE_SITTING`. | Supervised robot checklist items 7 and 3 in [robot.md](../skills/docs/robot.md#supervised-robot-checklist) |
| Pin the Python patch version the real backend runs with (currently `>=3.10,<3.11`). | Lab machine setup ([setup.md](setup.md)) |
| Manual Telegram test on a phone: busy reply and stop. Basic task and reply flow confirmed working. | Operator |
| Choose the onboard computer that runs the whole system on the robot ([architecture.md](architecture.md#design-decisions)) and have it ordered. | The student chooses; the advisor orders through university purchasing |
| Check whether the lab Wi-Fi allows Telegram and the LLM API. The university network is known to block Telegram; a lab hotspot is the stopgap. | The student |
| Set up the [OpenClaw 2 baseline](project.md#preparing-the-openclaw-2-comparison), including per-task session policy, the error and resubmission rules, skills logging their own timing and outcome, and re-measuring compaction on version 2. | The student, after this system runs smoothly on the robot; whether the baseline is tuned and the session policy are confirmed with the advisor |
