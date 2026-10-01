# Project

## What this is

A final-year, full-year capstone project at Bar-Ilan University. The lab's Unitree **Go2 EDU** quadruped is controlled in natural language: an operator writes a task in Telegram (or on the CLI), an LLM turns it into a plan of skill calls, and each skill drives the robot through the Unitree SDK over DDS. There is no ROS.

The dispatcher is also a measurement instrument. Every task writes a complete run log ([run-log.md](run-log.md)) from which tokens, latency and replanning can be computed. That log is the dataset for the study below, so many design choices favour comparable measurements over convenience (see [architecture.md](architecture.md#design-decisions)).

Scope: simulation (a stub backend) plus a small hardware demo on the real robot. Whether the participant study (below) runs on the real robot or the stub is open ([roadmap](roadmap.md#open-questions)). The student has computer-science coursework but no hands-on control or RL background, so the design favours simple, inspectable mechanisms over clever ones.

### Predecessor

A previous student, Tamir Ashwal, built a first version (repository `TamirAshwal/Go2`, see [references.md](references.md)). That repository held only four skill scripts (`walk`, `sit`, `stretch`, `detect_object`, about 164 lines in total), a static docs site and a Word file. It had no dispatcher code and no OpenClaw configuration: runtime behaviour came entirely from OpenClaw plus the prose in each `SKILL.md`. His research question (enforcing restrictions at prompt level versus through tool profiles) is retired.

This repository is new, not a fork. Only the skill logic was ported; `walk` was split into `walk` and `turn` (see [skills.md](skills.md)).

## Research direction

### The advisor's requirements

The advisor's original complaint about the inherited system was that the LLM guided the robot step by step, which made it slow. He asked for the LLM to produce an **up-front plan with explicit stop-and-report points** at which it can replan. The plan contract (`PLAN` / `DONE` / `ABORT`, `replan_after`, the planning horizon) exists because of this; it is specified in [loop-and-context.md](loop-and-context.md).

He also defined what counts as a failure (skill error, bounds rejection, timeout); the dispatcher's failure budget follows that definition ([loop-and-context.md](loop-and-context.md)).

### Main study

Proposed by the advisor. Participants interact with the Go2 under several **skill-library granularities**: atomic, medium and composite. Measures:

- dispatcher load: tokens, replanning frequency, latency;
- subjective trust and interaction quality.

Motivating paper: **OpenGo**, a parameterised skill library on a real Go2 ([references.md](references.md)). It found medium granularity (fixed execution functions with LLM-tunable parameters) to be the sweet spot. Low-level primitives are flexible but slow, context-hungry and accumulate errors; high-level macros are efficient but opaque and cannot adapt mid-execution.

The current skill set (five skills) is the atomic tier. Other tiers are loaded as separate skill folders (`skills.dir`, [configuration.md](configuration.md)); building them is on the [roadmap](roadmap.md#future-ideas).

The **planning horizon** (maximum steps per plan) is one config value. Horizon 1, k and "large" give one-step, batch and full-plan behaviour on the same code path.

### Study design

- **Two phases.** Phase one characterises the system objectively, in simulation and from run logs, before any human participants are involved. Phase two is the participant study.
- **Granularity and horizon are independent variables.** Varying both at once makes effects impossible to attribute. The experiment design must decide how they are crossed (open, [roadmap](roadmap.md#open-questions)).
- Experiments run only after the v2 verification layer is implemented ([roadmap](roadmap.md#v2-plan)). Everything that gates them, including the still-undesigned participant protocol, task set and trust measure, is listed in [roadmap.md](roadmap.md#before-experiments).

### OpenClaw as a system-level baseline

OpenClaw (the runtime the predecessor used) is included as a **system-level** baseline. Why this project does not run on OpenClaw is explained in [architecture.md](architecture.md#why-a-purpose-built-dispatcher).

- Assumption: every experimental task can run on both systems.
- Open: whether OpenClaw runs under one granularity condition or all of them. Decide when designing the task set ([roadmap](roadmap.md#open-questions)).
- **Caveat for any write-up:** requiring an explicit plan changes model behaviour, and the two systems also differ in context design, prompt, compaction and loop. Differences are therefore attributed to the **system as a whole**, never to planning or to any single component.

### Not a research question

"Does explicit planning help?" is explicitly **not** a research question. No internal step-by-step baseline is planned, and the OpenClaw comparison cannot answer it either (see the caveat above).

## People and roles

| Person | Role |
|---|---|
| Shalom Prinz | Student; designs and builds the system. |
| The advisor (professor) | Drives the research direction and reviews design documents. Prefers to plan together and asks pointed technical questions; design changes that affect the study go through him. |
| Achiya | Works with the lab's Go2 at the low level. The person to ask about robot-side facts: SDK topics, odometry, state fields. |
| Tamir Ashwal | Previous student; author of the predecessor system and its skill scripts. |

## Current status

- **v1 is implemented** and tested against the stub backend: the dispatcher loop, five skills, the stop path and limits, CLI and Telegram transports, and the run log.
- **The real backend has never been run on the robot.** Robot-side values (posture thresholds, settle waits, stop latency, `Move` while lying down) are unverified; the supervised checklist in [robot.md](robot.md) resolves them.
- **The LLM request has not been verified against the live API** (Sonnet 5.5 with `thinking: between_tools` and auto tool choice). See [llm.md](llm.md).
- All numeric limits are starting values to be tuned from logs and robot runs.
- Next: the v2 verification layer, then experiment design. See [roadmap.md](roadmap.md).
