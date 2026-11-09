# Project

## What this is

A final-year, full-year capstone project at Bar-Ilan University. The lab's Unitree **Go2 EDU** quadruped is controlled in natural language: an operator writes a task in Telegram (or on the CLI), an LLM turns it into a plan of skill calls, and each skill drives the robot through the Unitree SDK over DDS. There is no ROS.

The dispatcher is also a measurement instrument. Every task writes a complete run log ([run-log.md](../dispatcher/docs/run-log.md)) from which tokens, latency and replanning can be computed. That log is the dataset for the study below, so many design choices favour comparable measurements over convenience (see [architecture.md](architecture.md#design-decisions)).

Scope: simulation (a stub backend) for development and phase-one characterisation; the participant study (below) runs on the real robot. The student has computer-science coursework but no hands-on control or RL background, so the design favours simple, inspectable mechanisms over clever ones.

### Predecessor

A previous student, Tamir Ashwal, built a first version (repository `TamirAshwal/Go2`, see [references.md](references.md)). That repository held only four skill scripts (`walk`, `sit`, `stretch`, `detect_object`, about 164 lines in total), a static docs site and a Word file. It had no dispatcher code and no OpenClaw configuration: runtime behaviour came entirely from OpenClaw plus the prose in each `SKILL.md`. His research question (enforcing restrictions at prompt level versus through tool profiles) is retired.

This repository is new, not a fork. Only the skill logic was ported; `walk` was split into `walk` and `turn` (see [skills.md](../skills/docs/skills.md)).

## Research direction

### The advisor's requirements

The advisor's original complaint about the inherited system was that the LLM guided the robot step by step, which made it slow. He asked for the LLM to produce an **up-front plan with explicit stop-and-report points** at which it can replan. The plan contract (`PLAN` / `DONE` / `ABORT`, `replan_after`, the planning horizon) exists because of this; it is specified in [loop-and-context.md](../dispatcher/docs/loop-and-context.md).

He also defined what counts as a failure (skill error, bounds rejection, timeout); the dispatcher's failure budget follows that definition ([loop-and-context.md](../dispatcher/docs/loop-and-context.md)).

**Latency is a comparison dimension in its own right.** A design that plans better but responds much more slowly is a trade-off, not a win, and an operator who waits half a minute after the first prompt gives up. Latency is measured per call kind (first plan, follow-up, replan) from the run log; no target is set yet.

The goal is a paper that proposes the design and tests it. A design that only works is not a result, so the system must reach the comparison: objective measures from run logs and a study with participants.

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
- **Latency target: 4 s or less per LLM call**, set by the advisor. It is read as the median `total_ms` across all call types, with p90 reported beside it, because a median alone would hide a long worst case. `go2 report` computes it ([run-log.md](../dispatcher/docs/run-log.md#computing-the-study-metrics)).

### OpenClaw as a system-level baseline

OpenClaw (the runtime the predecessor used) is included as a **system-level** baseline. Why this project does not run on OpenClaw is explained in [architecture.md](architecture.md#why-a-purpose-built-dispatcher).

- The baseline is **OpenClaw 2**, set up and run by the student with this project's skills. The predecessor's original OpenClaw install is not used: OpenClaw has moved to version 2, and a baseline on the old version would not use the same skills. Setting it up comes after this system runs smoothly on the robot.
- Assumption: every experimental task can run on both systems.
- Open: whether OpenClaw runs under one granularity condition or all of them. Decide when designing the task set ([roadmap](roadmap.md#open-questions)).
- **What is compared.** Only "first call" and "later calls" exist on both systems: OpenClaw has no plans, so the finer call types apply to this system only. One call here can plan several steps, while an agent runtime may call the model per step, so per-call latency alone misleads. The headline numbers are therefore per task: time to first motion and total model wait. Whether OpenClaw 2's logs give per-call latency and tokens is not yet verified; if they do not, a logging proxy in front of the model API captures both.
- **Progress messages match.** The OpenClaw side shows Telegram progress at the same level of detail as this system ([running.md](running.md#progress-message)): plan, current step, outcome. Progress changes how long a wait feels, so a difference would skew the trust and interaction measures. If OpenClaw 2 cannot match it, the difference is reported as part of the comparison.
- **Caveat for any write-up:** requiring an explicit plan changes model behaviour, and the two systems also differ in context design, prompt, compaction and loop. Differences are therefore attributed to the **system as a whole**, never to planning or to any single component.

### What the predecessor's logs show

Analysis of the predecessor's OpenClaw session logs: 23 sessions and 105 runs, all with a preceding OpenClaw version and its own four skills. Per-turn timing exists only for the 12 July-August sessions on `claude-sonnet-4-6`. There is no robot-state log, so "success" there means the run ended, not that the task worked. These numbers set expectations; they are not baseline results.

| Measure (seconds) | n | Median | p90 | Max |
|---|---|---|---|---|
| LLM turn | 306 | 3.1 | 5.9 | 23.0 |
| Task with 1 skill | 22 | 10.5 | 16.2 | 20.4 |
| Task with 2-3 skills | 16 | 20.5 | 41.8 | 43.9 |
| Task with 4+ skills | 10 | 71.7 | 115.7 | 125.8 |
| First motion, from the Telegram message to the call being issued | 42 | 11.4 | 21.9 | 276 |

- **LLM target.** 75% of LLM turns (230 of 306) and 48% of first-in-run turns (26 of 54) were under the 4 s per-call target.
- **First motion.** From the Telegram message, the median is 5.0 s when turn 1 moves (n=11) and 11.6 s when turn 2 moves (n=27), usually after reading `SKILL.md`. From run start, the same medians are 3.4 s and 9.2 s, and the overall median is 8.0 s. The first run of a fresh session waits a median 10.7 s before the run starts, against 1.2 s later. Telegram time has 1 s resolution.
- **Time split.** About 60% LLM and 40% tools (48 tasks). Median 3 turns per task, p90 9, maximum 21.
- **Context.** 27-58k input tokens per call, about 85% of it fixed (inferred from the fresh-session floor), with 36 tools. This system uses 1.2-2.0k per call, measured on other models. Total input per task is a closer comparison than per call, since a task here also makes several calls: median 3.5-7.2k and maximum 25.7k here.
- **Latency.** Turn latency follows output tokens (Spearman 0.63), not input tokens (0.11, over a narrow range). Input tokens grew with the session (0.85) while first-turn latency did not (-0.02). The smaller context here is therefore not expected to reduce latency by itself.
- **Compaction and retries.** See [architecture.md](architecture.md#why-a-purpose-built-dispatcher). No overflow errors occurred. SDK-level retries are not logged, so they cannot be counted.
- **Errors.** 13 of 101 non-heartbeat runs ended in an error or abort, mostly provider faults. Recovery was the operator resubmitting.
- **Horizon.** One step at a time, and emergent: no instruction sets it. Of 474 turns, 364 made one call, 100 none and 10 several, none with parallel skills. Of 185 moves, 86 were followed directly by a detect. In August, 46 of 245 skill executions chained skills with `&&`, up to 8, with nothing validating the chain.
- **Behaviour.** Unverified success claims, a search that oscillated for 33 turns until aborted, a short command that re-ran an earlier failed task, and the agent editing skill scripts. There is no stop tool and approaches had no obstacle check.

### Preparing the OpenClaw 2 comparison

What the logs imply for the baseline. Details of the harness are decided when it is set up; the task set is settled with the advisor ([roadmap](roadmap.md#open-questions)).

- **Tune the baseline.** Reduced tools and inline skill docs, rather than the defaults, so the comparison is not against a strawman. The predecessor's `SKILL.md` read turn and full tool profile would otherwise count against OpenClaw for avoidable reasons. Report what was tuned.
- **Pin and log the settings** that move the numbers: tool profile, thinking level, session reset, heartbeat, cache retention and exec `yieldMs` (at 10,000 ms, skills longer than that return control while still running). The predecessor set almost none of these.
- **Session policy.** A fresh session per task adds startup delay; a shared session grows the context and allows earlier tasks to affect later ones. Choose one, state it, and measure its effect on first motion.
- **Multi-skill tasks.** Task time grows fastest here, and the logs have only 10 such tasks, so the task set needs more. Candidates: find three distinct objects and then take a posture; search for a hidden object within a radius; find a person, approach, wait and return; a 360° scan. Wait steps need a skill on both sides.
- **One clock on both systems.** The logs lack tool start events, physical motion, robot state, task outcome and retries. Skills write these themselves, so both systems are measured the same way, and a logging proxy covers retries.
- **Horizon as a measure.** Log the steps committed per model call and the chain length, because OpenClaw's horizon is not fixed at one step. This remains descriptive ([not a research question](#not-a-research-question)).
- **Error and resubmission rules, set before running.** Decide how provider errors count and whether a resubmitted task is one task or two, since merging runs inflates task time.
- **Progress.** The predecessor's OpenClaw had no plan or step structure and a median of one logged outbound message per task, so matching this system takes explicit work.
- **Wrong-context actions.** Count actions caused by earlier session content.
- **Cache.** 22 of 39 first turns with the same prompt hash and a gap under 5 minutes still went cold, unexplained, with a penalty of about 1.3 s. Record cache hits per call on both systems.
- **Re-measure compaction.** These figures come from an older version, so OpenClaw 2 may behave differently.

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
- **The real backend has run on the robot** in supervised runs with OpenRouter free models (logs in `logs/openrouter/real/`). Robot-side values not yet measured by a run stay open in the supervised checklist in [robot.md](../skills/docs/robot.md).
- **The Claude request has not been verified against the live API** (Sonnet 5.5 with `thinking: between_tools` and auto tool choice); the OpenRouter request has. See [llm.md](../dispatcher/docs/llm.md).
- All numeric limits are starting values to be tuned from logs and robot runs.
- Next: latency, measured with Claude; then the v2 verification layer and experiment design. See [roadmap.md](roadmap.md).
