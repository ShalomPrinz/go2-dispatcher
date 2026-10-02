# Documentation

These docs are the single source of project knowledge for the Go2 LLM dispatcher: what the project is, why it is built this way, how it behaves, and what comes next. Together with the code it should be enough to pick up the project without any other source.

New to the project? Read [project](project.md), then [architecture](architecture.md), then [loop and context](../dispatcher/docs/loop-and-context.md). To get something running, go to [setup](setup.md) and [running](running.md).

## Documents

Each document owns one topic. Other documents link to the owner instead of repeating it.

Docs live in four folders: shared documents that cover both services in `docs/` (this folder), dispatcher documents in `dispatcher/docs/`, skill documents in `skills/docs/`, and the test-infrastructure document in `tests/docs/`.

### Shared (`docs/`)

Documents that cover the whole system or both services.

| Document | Owns |
|---|---|
| [project.md](project.md) | What the project is and why it exists: research direction and study design, the advisor's requirements, OpenClaw as a system-level baseline and its caveat, what is explicitly not a research question, people and roles, current status. |
| [architecture.md](architecture.md) | Components and data flow, process model, repository layout, glossary, why a purpose-built dispatcher instead of OpenClaw (with what was lost and how it is covered), cross-cutting design decisions. |
| [safety.md](safety.md) | Everything that limits or stops the robot: the stop path and stop word, step timeouts, task time limit, motion budget, single-instance lock, orphan handling, the e-stop, supervised-operation rules. |
| [configuration.md](configuration.md) | Every config key (type, default, tunable or not), `.env` and secrets, precedence, CLI overrides, path resolution, validation rules. |
| [setup.md](setup.md) | Installing on a development machine and on the lab machine (uv, robot and vision extras, CycloneDDS troubleshooting, network interface, YOLO weights), Python version, verifying an install. |
| [running.md](running.md) | Operating the system: CLI commands and exit codes, `batch` files, Ctrl+C, Telegram setup, commands and replies, stub vs real backend, switching skill sets. |
| [roadmap.md](roadmap.md) | What comes next: the v2 plan (verification layer, verdicts, verifiability per skill, preconditions, escalation, `ASK`, stub upgrade, existing seams), future ideas, open questions (including experiment design), the gate before experiments, and pending human work with owners. |
| [references.md](references.md) | External documentation and papers, each with one line on why it matters to the project. |

### Dispatcher (`dispatcher/docs/`)

The `dispatcher` package: the loop, the LLM layer and the run log.

| Document | Owns |
|---|---|
| [loop-and-context.md](../dispatcher/docs/loop-and-context.md) | The dispatcher loop: the `submit_plan` plan contract, plan validation and the whole-plan pre-check, return reasons, what counts as a failure and as an LLM call, budgets, task outcome codes and their operator messages, the fixed-slot context layout and every fixed text the model sees. |
| [llm.md](../dispatcher/docs/llm.md) | The LLM layer: provider and model choice, Sonnet 5.5 parameter constraints and the request we send (auto tool choice, non-strict tools, minimal thinking via `between_tools`, no temperature, `max_tokens`), schema retry, horizon rejection (not truncation) and its monitoring rule, infrastructure retries, live-API verification status. |
| [run-log.md](../dispatcher/docs/run-log.md) | The run log as the study dataset: files, envelope, every record type, `index.jsonl`, and how to compute each study metric from the log. |

### Skills (`skills/docs/`)

The `skills` package: the skill contract and the robot side.

| Document | Owns |
|---|---|
| [skills.md](../skills/docs/skills.md) | The skill contract: `SKILL.md` format, invocation, the common response schema, error codes, policies (timeout and motion cost), catalog generation and registry hash, the stub backend and fault injection, how to add a skill. |
| [robot.md](../skills/docs/robot.md) | The Go2 EDU side: robot facts, SDK and DDS usage, state sampling and the posture rule, the real backend, settle waits, robot-side open questions (odometry, `stand` skill, `Move` while lying down), and the supervised robot checklist with its recorded results. |

### Tests (`tests/docs/`)

The test infrastructure, owned by the `tests-dev` agent.

| Document | Owns |
|---|---|
| [testing.md](../tests/docs/testing.md) | Running the tests: layout, markers and opt-in flags (live LLM, robot), helpers and fakes, golden files. |

The repository root also has a short [README](../README.md) (one paragraph, quick start, link here) and `CLAUDE.md` (working instructions for Claude Code sessions on this repo).

## Maintaining these docs

- **The docs are the source of truth** for why the project is built as it is and for the contracts others depend on (plan schema, skill response schema, config keys, outcome codes, log record types, fixed texts). The code is the source of truth for everything else it states unambiguously; do not restate it.
- **A change that affects behaviour updates the owning document in the same commit.** If the docs and the code disagree, fix one of them; do not leave the difference standing.
- **Decisions are recorded with their reasons in the owning document**, in a short "Design decisions" section: the decision, why, and any alternative that was seriously considered and rejected. There is no separate decisions log.
- **Open questions and pending work live in [roadmap.md](roadmap.md)**, each with an owner or the way it gets resolved. When one is resolved, move the result into the owning document (as a fact or a design decision) and remove it from the roadmap.
- **Describe current behaviour.** Mark starting values that are expected to be tuned as *tunable*. Mark facts not yet checked on the robot or against the live API as *unverified*. Robot checklist results go in [robot.md](../skills/docs/robot.md).
- **One purpose per document.** Link to the owner instead of copying; add a new document only for a topic no existing one owns, put it in the folder of the service it covers (or here if it covers both), and list it in the matching table above.
- **Keep only knowledge worth maintaining.** No process history (task numbers, commit lists, review rounds), no drafting artefacts.
- **No personal contact details** (emails, phone numbers). Names and roles only.
- Plain, concise, technical Markdown.
