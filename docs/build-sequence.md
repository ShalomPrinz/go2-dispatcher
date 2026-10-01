# v1 Build Sequence (orchestration file)

Throwaway file for the initial implementation run. Delete it once v1 is merged; durable decisions belong in `docs/decisions.md`.

The spec is in `docs/spec/` (four parts; § numbers are global across them). This file only defines **who does what, in what order** — the spec defines **what to build**.

---

## 1. Rules for the orchestrator (main agent)

You coordinate; you do not implement.

- **Never write or edit source code, tests, or docs yourself.** All implementation is done by general-purpose subagents, one task at a time, in the order of §3.
- **Strictly sequential.** Start a task only after the previous one is verified and committed. Never run two implementation subagents at once.
- **You may:** run `uv run pytest` and `git` commands, edit **this file only** (progress log), commit, and spawn subagents.
- **Do not read the spec** (`docs/spec/*`) or the source code. Subagents read what they need; your context must last the whole run. Point subagents at spec sections by number; never paste spec or code into briefs.

### Keeping your context small

- Read only this file. Re-read §3 for the next task's entry and §4 for handoff notes; nothing else.
- Test output: run `uv run pytest -q 2>&1 | tail -n 15`. Never print full output. If tests fail, do not diagnose — hand the tail to a fix subagent.
- Changes: use `git status --short` and `git diff --stat`; never print a full diff.
- "Done when" checks that need judgement (for example "matches the spec") are the subagent's job; you only check what a command can confirm (tests pass, a file exists, a CLI command runs).
- Subagent reports are capped at 300 words. If one is longer, record only the handoff essentials in §4.
- Handoff notes in §4: at most 3 lines per task.

### Per-task loop

1. Mark the task `in progress` in §4.
2. Spawn a general-purpose subagent with the brief built from the template in §2 (task text from §3 + handoff notes from earlier tasks in §4).
3. When it returns, verify with commands only:
   - `uv run pytest -q 2>&1 | tail -n 15` passes (all tests, not only the new ones);
   - the command-checkable "Done when" items hold (files exist, listed commands run);
   - `git status --short` shows no files outside the task's scope that the report doesn't mention.
4. If verification fails: spawn a **new** fix subagent with the same brief plus the failure output and the previous subagent's report. At most **2 fix attempts** per task. If it still fails, stop the whole run and report to the user.
5. Write the subagent's handoff notes into §4 (keep them short: names of public functions/classes created, deviations, anything the next tasks must know).
6. Commit: `git add -A && git commit -m "v1 T<n>: <task title>"`.
7. Mark the task `done` and continue.

### When a subagent reports a spec gap

It should already have chosen the simplest option consistent with the spec and recorded it in `docs/decisions.md`. Accept that unless it contradicts the spec; if it does, send a fix subagent. Never stop to ask the user about a gap that §20 (Open decisions) or a reasonable default covers.

### At the end

Report to the user: tasks completed, decisions recorded in `docs/decisions.md` that the spec did not cover, anything not finished, and the final `uv run pytest` summary.

---

## 2. Brief template for subagents

Each subagent starts with no context. Send this, filled in:

```
You are implementing one task of the Go2 LLM dispatcher v1, in this repository.

Read first:
- The header of docs/spec/01-foundations.md (conventions; § numbers are global across docs/spec/*).
- These spec sections, completely: <SECTIONS>
- Any existing code you will build on: <FILES TO READ>

Your task: T<n> — <TITLE>
<SCOPE>

Done when:
<DONE WHEN>

Notes from earlier tasks:
<HANDOFF NOTES FROM §4, only those relevant>

Rules:
- Stay within this task's scope. Do not implement later tasks. Small fixes to earlier code are allowed only if needed for this task; list them in your report.
- Follow the spec exactly. Where it is silent, choose the simplest option consistent with it and add a short entry to docs/decisions.md (create the file if missing).
- Every (sketch) value goes in config or a named constant.
- Run `uv run pytest` before finishing; everything must pass.
- Do not commit; the orchestrator commits.

Report back (under 300 words):
1. Files created/changed.
2. Public names other tasks will use (classes, functions, signatures).
3. Decisions you made that the spec did not cover.
4. Anything incomplete or fragile.
```

---

## 3. Tasks

M-numbers refer to the milestones in §22. M8 (real robot) is **not** part of this run: the real-backend code is written in T2/T3 but only tested on the lab machine later.

### T1 — Project scaffold and configuration (M1)
- **Sections:** §1, §2, §3, §4, §5, §6.6, §6.7, §19 intro.
- **Scope:** repository layout (empty packages with `__init__.py`), `pyproject.toml` exactly as §4.1, `config.example.toml`, `.env.example`, `.gitignore`, minimal `README.md`; `go2_dispatcher/config.py` (models, validation, base dir and path resolution, `.env` parser), `clock.py`, `process_lock.py`, the exception classes in `models.py`; `tests/conftest.py` with `--run-live`, `--run-robot`, `--update-golden`; `tests/helpers/` package with `make_config`.
- **Done when:** `uv lock` and `uv sync` succeed on this machine (if the `robot` extra blocks locking, apply the §4.1 fallback and record it); config and process-lock unit tests from §19.2 pass.

### T2 — Skill runtime: helpers and backends (M1)
- **Sections:** §7, §9, §8.6, §8.7, §6.2.
- **Scope:** `go2_skills/policy_base.py`, `result.py` (capture_stdout, orphan watchdog, build_response, emit, run_skill, write_raw_stdout), `backend.py`, `posture.py`, `coco.py` (the list below, in this order — it is the list from the original detect_object SKILL.md), `stub.py` (state file, faults, noise, detections), `real.py` (written per §9.1, lazy imports, not executed in tests), utilities `stop_move.py` and `read_state.py`; `SkillResponse`/`RobotState`/`SkillError` models in `models.py`.
- **COCO_CLASSES:** person, bicycle, car, motorcycle, airplane, bus, train, truck, boat, traffic light, fire hydrant, stop sign, parking meter, bench, bird, cat, dog, horse, sheep, cow, elephant, bear, zebra, giraffe, backpack, umbrella, handbag, tie, suitcase, frisbee, skis, snowboard, sports ball, kite, baseball bat, baseball glove, skateboard, surfboard, tennis racket, bottle, wine glass, cup, fork, knife, spoon, bowl, banana, apple, sandwich, orange, broccoli, carrot, hot dog, pizza, donut, cake, chair, couch, potted plant, bed, dining table, toilet, tv, laptop, mouse, remote, keyboard, cell phone, microwave, oven, toaster, sink, refrigerator, book, clock, vase, scissors, teddy bear, hair drier, toothbrush
- **Done when:** unit tests for `build_response` and `derive_posture` pass; integration tests for both utilities on the stub pass (contract, `backend_not_configured`, noise, fresh-interpreter import test).

### T3 — The five skills (M1)
- **Sections:** §7, §8.1–§8.5, §9.2, §19.4 (skill contract, stub behaviour, orphan watchdog).
- **Scope:** `walk.py`, `turn.py`, `sit.py`, `stretch.py`, `detect_object.py` with their policies; the five `skills/<name>/SKILL.md` files (frontmatter exactly as §8, plus a short human prose body each).
- **Done when:** all §19.4 skill contract, stub behaviour and orphan watchdog tests pass; each skill runs by hand on the stub (`GO2_BACKEND=stub python -m go2_skills.walk '{"direction":"forward","distance_m":1}'`).

### T4 — Models and registry (M2)
- **Sections:** §6 (all), §7.1, §7.2, §10.
- **Scope:** remaining models in `models.py` (Plan, StepResult, StopMoveResult, TaskOutcome, TaskSummary, MotionCostModel); `policies.py` re-export; `registry.py` (loading, validation, catalog, registry hash function); `tests/golden/catalog.txt` exactly as §10.
- **Done when:** registry unit tests from §19.2 pass, including the golden catalog and every `RegistryError` case.

### T5 — Bounds, precheck, motion budget (M2)
- **Sections:** §13.2, §13.3, §13.4, §11.8 (motion-budget message only).
- **Scope:** `bounds.py`, `budget.py`, `precheck` (place it in `bounds.py` unless the spec says otherwise).
- **Done when:** bounds, precheck and motion budget unit tests from §19.2 pass.

### T6 — Prompts, renderer, context builder (M3)
- **Sections:** §11 (all).
- **Scope:** `prompts.py` (every fixed text in §11), `render.py`, `context.py` (a function that builds the user message from per-task state; define a small input dataclass for that state and report its fields); golden files under `tests/golden/`.
- **Done when:** prompts and render + context unit tests from §19.2 pass.

### T7 — Plan validation and LLM client (M3)
- **Sections:** §12 (all), §13.1.
- **Scope:** `validation.py`, `llm.py` (`plan_tool_schema`, `AnthropicPlanner`, infra retries, interruption); `tests/helpers/` `ScriptedPlanner`.
- **Done when:** tool schema, plan validation and LLM client (MockTransport) unit tests from §19.2 pass. No test touches the network.

### T8 — Executor (M4)
- **Sections:** §14 (all), §6.3, §9.2 (faults).
- **Scope:** `executor.py` (run, kill_current, stop_move, read_state, locking per §14.3); test-only helper skill module in `tests/helpers/` for the env-secret test.
- **Done when:** all §19.4 executor integration tests pass.

### T9 — Run log and dispatcher loop (M5)
- **Sections:** §15 (all), §17 (all), §18, §11.5, §19.3.
- **Scope:** `runlog.py`, `dispatcher.py`; `tests/helpers/` `FakeClock`, `FakeExecutor`.
- **Done when:** all 28 loop tests in §19.3 and the §19.4 end-to-end tests pass.

### T10 — CLI (M6)
- **Sections:** §16.1, §16.2, §5.4, §19.4 (CLI).
- **Scope:** `transports/__init__.py` (`load_config_and_env`, `build_dispatcher`, `format_outcome`), `transports/cli.py`; `tests/helpers/planner_factory.py`.
- **Done when:** §19.4 CLI tests pass; `uv run go2-dispatch catalog` prints the catalog. If `ANTHROPIC_API_KEY` is set in the environment, also run the `live_llm` test with `--run-live` and report the result; otherwise say it was skipped.

### T11 — Telegram bot (M7)
- **Sections:** §16.1, §16.3, §11.8 (transport texts), §19.4 (Telegram).
- **Scope:** `transports/telegram_bot.py` with `build_application`, handlers, `post_stop`.
- **Done when:** §19.4 Telegram handler tests pass. (The manual phone test is for the user, not this run.)

### T12 — Documentation (M9)
- **Sections:** §21, plus whatever each doc covers; read the implemented code, not only the spec.
- **Scope:** every file in §21 except `docs/spec/*` (already present) and `docs/decisions.md` (append, don't rewrite: add the decisions listed in the spec's §21 row as entries, keeping entries added by earlier tasks). `docs/testing.md` includes the robot checklist from §19.5. `docs/open-decisions.md` mirrors §20.
- **Done when:** every §21 file exists; config keys in `docs/configuration.md` match `config.py`; commands in `docs/running.md` match the CLI's `--help`.

### T13 — Independent conformance review
- **Sections:** all four spec parts.
- **Scope:** a fresh subagent that did not implement anything reviews the code against the spec and runs `uv run pytest`. **It does not edit files.** It returns a list of deviations (section, file, what differs, severity).
- **Then:** for every blocker or major deviation, the orchestrator runs a fix subagent (one per deviation group, sequentially, same verify/commit loop). Minor deviations are listed in the final report instead.
- **Done when:** no blocker or major deviations remain and `uv run pytest` passes.

---

## 4. Progress log

The orchestrator updates this section after every task. Keep handoff notes short.

| Task | Status | Commit | Handoff notes |
|---|---|---|---|
| T1 | done | 031106a | src/ layout. `config.load_config(path, overrides)`, `build_config`, `load_config_and_env` (in config.py; T10 re-exports), `parse_env_file`, `load_env_file`; overrides are dotted keys. `clock.Clock/MonotonicClock`, `process_lock.acquire(log_dir)`. models: ConfigError, RegistryError, BusyError, LLMUnavailable(detail), LLMInterrupted(cause). <br>`coco.py` already created in T1 (T2 keeps it). Tests: `from helpers import make_config, REPO_ROOT`; `make_config(tmp_path, loop={...})`; `update_golden` fixture. robot extra locked fine (no fallback). |
| T2 | done | b581eab | models: SkillError, RobotState, SkillResponse. result: `run_skill(skill, body, *, sample_state=True)`, `InvalidParams`, `build_response`, `emit`, `capture_stdout`, `start_orphan_watchdog`, `parse_params`. backend: `get_sport_client`, `get_detector`, `sample_state`, `sleep`, DetectorError subclasses (CameraUnavailable, BadFrame, WeightsMissing; `.code`), BackendNotConfigured. <br>stub: `read_posture`, `write_posture(posture, path=None)` (for --reset-stub), STUB_ERR_* constants. Utilities strip GO2_STUB_FAULT; missing backend → backend_not_configured. <br>Test helpers: `stub_env(tmp_path, detections=None, fault=None, **extra)`, `run_module(name, params, env)`, `single_response(proc)`. Fault/crash/orphan tests are T3's. |
| T3 | done | (pending hash) | Each skill module: `SKILL`, `POLICY`, `body(params)`; policies WalkPolicy, TurnPolicy, SitPolicy, StretchPolicy, DetectObjectPolicy (TIMEOUT_S/SETTLE_S constants). `motion.py`: require_enum, require_number, move_loop, single_action. <br>No param defaults in skills; extra keys ignored; orphan break reports ok if StopMove succeeds. <br>Skill fault tests check raw process outcome only; executor mapping to malformed/timeout is T8's. |
| T4 | pending | | |
| T5 | pending | | |
| T6 | pending | | |
| T7 | pending | | |
| T8 | pending | | |
| T9 | pending | | |
| T10 | pending | | |
| T11 | pending | | |
| T12 | pending | | |
| T13 | pending | | |
