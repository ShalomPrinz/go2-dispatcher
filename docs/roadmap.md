# Roadmap

What comes next. When an item here is resolved, move the result into the owning document (as a fact or a design decision) and delete it from this page.

## v2 plan

Agreed direction with the advisor. The study's experiments run only after v2 is implemented ([project.md](project.md#study-design)).

### Verification layer

Lives inside the dispatcher and checks each step against the robot's sampled state. It has two separate components, because one is universal and the other is not:

- **Fault detection**: did the robot enter a bad state? Fallen (IMU roll/pitch), stuck or faulted (foot force, `mode`), command rejected (SDK return code). Universal; needs no knowledge of what the step was meant to do.
- **Goal verification**: did the skill achieve what was asked? Skill-dependent; needs an expectation and a measurement.

Conceptual basis: DoReMi, detecting and recovering from plan–execution misalignment ([references.md](references.md)).

### Verdicts

Each step gets one of four verdicts:

| Verdict | Meaning |
|---|---|
| `verified` | The measured outcome matches the expectation. |
| `failed` | The measured outcome contradicts the expectation, or a fault was detected. |
| `unverified` | Nothing measurable exists for this skill. |
| `unavailable` | The state read failed. |

Four rather than two, so that "could not check" is never reported as "worked".

### Verifiability per skill

Assuming no usable position estimate:

| Skill | Verifiable | How |
|---|---|---|
| `sit`, `stretch` | yes | `mode`, body height |
| `turn` | yes | IMU yaw delta; drift is negligible over a few seconds |
| `walk` | no | needs a position estimate |
| `detect_object` | trivially | self-reporting |

**Dependency on odometry.** Whether the robot's own position estimate is usable is open ([pending work](#pending-human-work)). `SportModeState` has `position` and `velocity` fields, and Go2 topic lists include `rt/utlidar/robot_odom` and `rt/utlidar/robot_pose`. v1 already logs `position` and `velocity` from `rt/sportmodestate` in every state sample. If the estimate is usable, `walk` becomes verifiable (with a drift tolerance) and a geofence becomes possible. Keep this distinction:

- the robot's **own measurement-based estimator** is acceptable as a verification source;
- **dead reckoning from commanded velocity** is not, because it restates the walk skill's own assumption (walk is open-loop: it commands a speed for a time).

### Preconditions

Before a step is dispatched, check that the skill is valid from the current state (for example, no walking while sitting). A violation is rejected with the observed state, before the robot moves.

### Escalation

On a `failed` verdict, abandon the rest of the plan and call the model again with the observed state and the failure. This is the stop-and-report behaviour the study is about, and the source of the replanning-frequency metric.

### `ASK` plan status

A dedicated status for clarifying questions (for example "go to the chair" when there are two chairs). In v1 the model returns `ABORT` with the question in `message`.

### Stub upgrade (prerequisite)

The stub must integrate commanded velocity and hold a simple state. Without it the verifier reports `unavailable` for everything during offline development. Today the stub remembers only sitting or standing ([skills.md](skills.md)).

### Seams already in v1

Kept on purpose; do not remove them as dead code.

- `StepResult.verification`, always `"unverified"` in v1.
- `state_before` / `state_after` on every skill response and `state_after` on every `StopMove`, including the robot's own `position` estimate when published.
- The `expect` key is accepted (and ignored) in `SKILL.md` frontmatter, for per-skill expectations.
- A single posture rule, `derive_posture()` in `skills/posture.py`.
- Plan status normalised in one place (`Plan._normalise_status` in `models.py`), so adding `ASK` is local.
- The stub state file and stub `sample_state()`, which the stub upgrade extends.
- `skills.dir` is configurable, so other granularity tiers can be loaded.

## Future ideas

Not scheduled.

- **Further granularity tiers** (medium, composite) as separate skill folders selected with `skills.dir`. This is the main study's manipulated variable, so it is needed before the participant study.
- **Forward messages received during a run to the LLM.** Decide between letting the model update the plan in place (the dispatcher would need to support replacing a running plan) and limiting it to classifying the message as a stop or replying to the sender with text. In v1 only the stop word is recognised during a task; other messages get a busy reply.
- **Prompt caching** for the static slots (tools, system text, catalog). Reduces cost, but cached tokens are reported separately and cache behaviour differs between skill sets of different sizes, so token comparisons would need uncached-equivalent reporting. The slot order already allows it.
- **Geofence**, if a position source exists (see odometry above).
- **Continuous monitoring during a skill**: a watchdog that can abort mid-motion rather than only between steps.
- **Independent batch tasks.** `batch` carries the previous task and posture across lines; experiments may need a reset per task (for example an `--independent` flag).
- **Send the optional `PLAN` message to the operator** as progress. Currently it is logged only.

## Open questions

| Question | Current v1 behaviour | Resolved by |
|---|---|---|
| Participant protocol: number of participants, session procedure, consent and ethics approval, participant safety around the robot beyond the [supervised-operation rules](safety.md#supervised-operation-rules). | Not designed | Experiment design with the advisor |
| Task set: which tasks participants give, and whether each can run on both systems (the OpenClaw-baseline assumption, [project.md](project.md#openclaw-as-a-system-level-baseline)). | Not designed | Experiment design with the advisor |
| How subjective trust and interaction quality are measured (questionnaire or other instrument). Dispatcher load is computed from the run log ([run-log.md](run-log.md#computing-the-study-metrics)). | Not designed | Experiment design with the advisor |
| How are skill granularity and planning horizon crossed in the experiment? | — | Experiment design with the advisor |
| Does the OpenClaw baseline run under one granularity condition or all of them? | — | Experiment design (task set) |
| `max_llm_calls` per horizon condition. At horizon 1 long tasks may hit the cap, so `CALL_BUDGET_EXHAUSTED` rates partly reflect the cap. | One value for all conditions, logged per task | Experiment design |
| Add a `stand` skill (`StandUp` then `BalanceStand`, with a settle wait)? No skill can stand the robot up: after `sit`, motion fails until a person stands it up, and in a batch run one `sit` affects every later task. | Not added; the model sees `Posture: sitting` and should `ABORT` with an explanation. Adding it is one `SKILL.md` plus one module. Recommended. | Decide before experiments |
| Final wording of the system text, notices and operator messages. It must be identical across conditions, because directive text steers behaviour. | Current texts in `prompts.py` ([loop-and-context.md](loop-and-context.md)) | Freeze before experiments |
| Is the horizon-rejection rate acceptable, or should long plans be handled differently? | Rejected, never truncated; rate logged ([llm.md](llm.md)) | Inspect `horizon_rejection` rates from runs |
| Jev (a "TypeSafe AI" decision model) as an alternative LLM layer? The planner only needs decision making plus quantified parameters (for example "walk 3"). | Under consideration, not decided | Advisor and student |
| Every starting value: budgets, limits, timeouts, parameter ranges, settle waits, posture thresholds. | Marked *tunable* in [configuration.md](configuration.md) and the topic docs | Tune from logs and robot runs |

## Before experiments

The gate for experiments with participants, collected from the sections above. Each item is specified where it is linked.

1. Resolve the [pending human work](#pending-human-work) below (live API check, robot checklist, lab-machine install, Telegram test, odometry answer).
2. Implement the [v2 plan](#v2-plan), including the stub upgrade.
3. Build the medium and composite skill tiers ([future ideas](#future-ideas)); decide the `stand` skill and whether `batch` needs a per-task reset.
4. Run phase one: characterise the system in simulation and from run logs ([project.md](project.md#study-design)); tune the starting values.
5. Settle the experiment-design [open questions](#open-questions) with the advisor: protocol, task set, trust measurement, how granularity and horizon are crossed, OpenClaw conditions, `max_llm_calls` per horizon.
6. Freeze the system text, notices and operator messages; from then on the registry hash must stay fixed within a condition ([skills.md](skills.md#catalog-and-registry-hash)).

## Pending human work

| Item | Owner / how |
|---|---|
| Is the robot's position estimate (odometry) available and usable? Decides `walk` verification and the geofence. | Ask Achiya |
| Live API check of the LLM request parameters (Sonnet 5.5, `between_tools`, auto tool choice). | `ANTHROPIC_API_KEY=... uv run pytest --run-live -s tests/integration/test_live_llm.py` ([testing.md](testing.md#live-llm-test)) |
| Supervised robot checklist: posture thresholds (`body_height` or `mode`), settle waits, what `Move` does while lying down, kill-to-stop latency. | Supervised run on the robot; record results in [robot.md](robot.md) |
| First run of the real backend; `uv sync --extra robot` (CycloneDDS build) on the lab machine; pin the exact Python version once it works (currently `>=3.10,<3.12`). | Lab machine setup ([setup.md](setup.md)) |
| Manual Telegram test on a phone: busy reply, stop. | Operator |
| Analyse the old OpenClaw session logs: count compactions, retries and context size per call, to turn the measurement-validity argument ([architecture.md](architecture.md#why-a-purpose-built-dispatcher)) into data. | Obtain the logs from the predecessor's lab machine |
