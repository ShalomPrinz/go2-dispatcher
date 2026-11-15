# Architecture refactor: shared context

Read this file first, in full. It is the shared background for a series of eight step prompts (`refactor-01-…md` to `refactor-07-…md`, with `refactor-02.5-…md` between steps 2 and 3), each run in its own session, in order. The step prompt tells you what to do; this file tells you why, where the whole series is going, and the rules every step follows.

These `refactor-*.md` files are working briefs. They live only on the `refactor_prompts` branch: read them from there (`git fetch origin refactor_prompts`, then `git show origin/refactor_prompts:<file>`), never copy or commit them onto a working branch, never merge that branch, never cite them from code or docs, and never copy their wording into docs (the docs carry no process history).

## Where the work lives

All steps are committed on one branch, **`architecture_refactor`**, created from the default branch by step 1. Every step session works like this:

1. Start: `git fetch origin architecture_refactor refactor_prompts`. Put the session's working branch on the tip of `origin/architecture_refactor` before changing anything (a fresh session branch has no commits of its own, so `git merge --ff-only origin/architecture_refactor` is enough). Check with `git log --oneline | head -20` that the earlier steps are there, and read the "Step status" section below.
2. Work: commit each task locally, as the rules below say.
3. End: when the step is done and the full check list is green, push the commits to the same branch as a fast-forward: `git push origin HEAD:refs/heads/architecture_refactor`. Never force-push it, never push a red or half-done step, and do not open a pull request unless the user asks.
4. Then add the step's notes for the next session to "Step status" below, as a commit on `refactor_prompts` that changes only this file. Keep each entry short: the commits, what ended differently from what the next prompt assumes, and deferred work.

## Step status

Paths in the step 1 and step 2 entries below are as those steps left them; step 2.5 moves `robot/` and `vision/` to `services/robot/` and `services/vision/` (module names `services.robot.…`, `services.vision.…`).

### Step 1 (`refactor-01-contract.md`): done

On `architecture_refactor`: `tooling: add contract/ to the ownership map and agent definitions under skills-dev` and `contract: extract the shared dispatcher/skills contract into a top-level contract package`. Registry hash `768d05c6633b8fdc` before and after; 554 passed, 1 skipped; 94 integration; 6 import contracts kept.

Notes for later steps:

- `contract/` modules: `schema`, `policy`, `manifest`, `env`, `wire` (was `skills/ipc.py`), `constants` (`YAW_SIGN`, and `Link` from `skills/state_store.py`, which three dispatcher modules imported only for that type). Doc: `contract/docs/contract.md`.
- The error-code enum is `contract.schema.ErrorCode`; `SkillError` is the response's error dataclass.
- **Deferred to step 2**: `dispatcher/config.py` still imports `skills.stub` (`StubPosture`, `FaultKind`, defaults, `parse_objects`) and `skills.coco`. It did not come apart cleanly: `parse_objects` is stub implementation and the transports call `stub.reset`.
- **Candidates for `contract/` when the services move**: the service lifecycle constants (`READY_LINE`, `STARTUP_TIMEOUT_S`, `STOP_GRACE_S`, `EXIT_LOCKED`) stay in the service modules, because the dispatcher also uses those modules as launch targets.
- Import contracts now: contract imports no project package (nor `tests`); contract imports neither pydantic nor yaml except `contract.manifest`; the parser is protected (importers: `dispatcher`, `skills.tests`, `contract.tests`); the three older contracts are kept. import-linter cannot express "standard library only"; the fresh-interpreter test in `tests/integration/test_skills.py` covers the heavy imports for every `skills` module and every `contract` module except `manifest`.
- **Order that works when a step adds a package** (a commit cannot be green in between, because no shims are allowed): `claude-config-dev` first, committed alone (the ownership test requires every mapped path to exist, so it carries a self-expiring exemption for the new folder; `TYPED_ROOTS` in `.claude/hooks/typecheck.py` waits until `pyproject.toml` lists the package). Then, uncommitted and in sequence: `tests-dev` packaging in `pyproject.toml` (`lint-imports` fails until the folder exists), the package agent's move, the other package's import updates, `tests-dev` for `tests/helpers/` and the import contracts, and a `claude-config-dev` follow-up (remove the exemption, add `TYPED_ROOTS`, update the `CONTRACTS` paths in `.claude/scripts/review_preview.py`). Review and commit these together as one green commit.
- Give the first package agent a brief that says the rest of the suite will fail on imports until the next agent runs, and ask it for the full old-to-new mapping for the next brief.
- The reviewer blocks on process-history wording in docs ("was read from", "still", "moved to"). Tell agents to write decisions in the present tense with a present-tense rejected alternative.
- Known flaky test, not caused by the refactor: `skills/tests/unit/test_vision_ipc.py::test_result_and_error_round_trip` fails now and then in full runs (`EADDRINUSE`: the second `serve_once` binds before the first server thread closes its listener). It passes alone. Not fixed; the fix is a separate commit when the user agrees.
- Whole-repo `uv run basedpyright` reports 13 errors at baseline (9 missing-extra imports, 3 in `dispatcher/tests/unit/test_console_trace.py`, 1 in `tests/integration/test_live_llm.py`); compare against that, not zero.

### Step 2 (`refactor-02-robot-vision-packages.md`): done

On `architecture_refactor`: `tooling: add robot/ and vision/ to the ownership map and agent definitions under skills-dev`, `architecture: move robot and vision services into robot/ and vision/ packages`, `tooling: name the robot service motion loop in reviewer check 4 and add robot/local.py to the safety files`, `docs: state what the dispatcher loads through the robot utilities and list the import-contract exceptions in the roadmap`.

Notes for later steps:

- Where things are, differently from the target layout: the stub fault code is its own module, `robot/faults.py` (not under `robot/backends/`); the stub state file is `robot/stub_state.py`; the sim is `robot/backends/sim.py`. Backend selection and the stub time scale are `contract/backend.py`, the process helpers (stdout capture, orphan watchdog) are `contract/process.py`, because skills use both. `describe_exception` is in `contract/schema.py`.
- The dispatcher starts the services from the `SERVICE_MODULE` strings in `robot/protocol.py` and `vision/protocol.py` (with `READY_LINE`, `STARTUP_TIMEOUT_S`, `STOP_GRACE_S`, and `EXIT_LOCKED` for the robot), not from `module.__name__`.
- **Ten import-contract exceptions** in `[tool.importlinter]` in `pyproject.toml`, each marked "exception, to be removed" and listed in `docs/roadmap.md` ("Import-contract exceptions"). They are step 3's to remove, where the import goes: `robot.stop_move` / `robot.read_state` (and the `test_stop_move` unit test) import `skills.runner`; `vision.detectors.stub` (and its unit test) imports `robot.stub_state`; `vision.detectors.real` imports `robot.backends.real` for `init_dds`; `dispatcher.executor`, `dispatcher.transports.telegram_bot` and `test_telegram_bot` import `robot.stop_move` / `robot.read_state` for the module name and `POLICY`, which also loads `robot.local` and the sim backend.
- `dispatcher/config.py` imports `robot.faults` (`FaultKind`), `robot.stub_state` (posture, defaults, `parse_objects`), `contract.backend` (`DEFAULT_TIME_SCALE`) and `vision.coco` (`COCO_CLASSES`). These are not under a contract exception, because the dispatcher contract forbids only `robot.backends` and the service-side modules; step 3 still moves them behind the robot client surface or `contract/`.
- `dispatcher/executor.py` still imports `skills.detect_object` (fault 5; step 4).
- The real-hardware code in `vision/detectors/real.py` and the real branch of `vision/service.py` is unverified on the robot (moved, not exercised).

### Step 2.5 (`refactor-02.5-services-package.md`): done

On `architecture_refactor`: `tooling: split skills-dev into contract-dev, robot-dev and vision-dev and map the services/ paths` and `architecture: move robot and vision into services/robot and services/vision under one services package`. Registry hash `768d05c6633b8fdc` before and after; 555 passed, 1 skipped; 94 integration; 9 import contracts kept; basedpyright 13 errors (baseline).

Notes for later steps:

- **Agents (user decision, overrides "No new dev agents" below)**: `contract-dev` owns `contract/`, `robot-dev` owns `services/robot/`, `vision-dev` owns `services/vision/`, `skills-dev` owns only `skills/`. The `services/` root files (`services/__init__.py`, `services/CLAUDE.md`) are under `main` in `.claude/ownership.json`, so the main session edits them; `packages` lists `services/robot/` and `services/vision/`, not `services/`, so the scope hook lets the main session write the root files. The user said "services is the main agent's responsibility": ask whether `services/backend/` (step 3) is also main-owned or goes to robot-dev before briefing it, and have claude-config-dev map it first.
- **Import contracts restructured** (user decision): an `independence` contract between `services.robot` and `services.vision`, and a `forbidden` contract `services` ↛ `skills`, `dispatcher`, replace the two per-service forbidden contracts. The ten exception edges are renamed and sit on the contract they break: the three `services.robot.{stop_move,read_state}` / `test_stop_move` → `skills.runner` edges on the services contract, the three vision → robot edges on the independence contract, the four dispatcher edges on the dispatcher contract. `tests/docs/testing.md` and `docs/roadmap.md` tables match; roadmap owners are now robot-dev / vision-dev.
- `services/` holds only `__init__.py` and `CLAUDE.md`; no `services/docs/` or `services/tests/`. The design decision is in `docs/architecture.md` ("Four top-level packages …").
- Integration helper modules `tests/integration/robot.py` and `tests/integration/vision.py` keep their names.
- Sub-agents with `model: sonnet` worked for the mechanical briefs; several left relative links one level short after the move. A markdown link check (every `](path)` resolves) caught them; run one after any move.

### Step 3 (`refactor-03-backend-interface-faults.md`): done

On `architecture_refactor`, 7 commits from `tooling: map the shared services/backend/ package to robot-dev …` to `docs: record the backend interface, the fault wrapper and the exception-free import contracts in architecture.md`. Registry hash `768d05c6633b8fdc` before and after, goldens unchanged; 590 passed, 1 skipped; 96 integration; 14 import contracts kept, **no exceptions left** (roadmap section gone); basedpyright 13 errors (baseline).

Notes for later steps:

- **User decisions**: the per-step robot fault travels in a **separate sim-only fault file** (`contract.faults.fault_file(state_file)` = `<state file>.fault.json`), written by the dispatcher executor before `arm` and consumed by the sim backend at arm; the wire has no `fault` field and `PROTOCOL_VERSION` stays 1. Skill faults (hang, crash, garbage, error) fire from one hook, `skills.faults.fire()`, in `run_skill` before the body (accepted edge case: a fault on `detect_object` fires before the target check). `services/backend/` is owned by robot-dev.
- **New modules**: `contract/runtime.py` (generic process runtime: `run_main`, `StateSampler(reader, prepare=…)`, `SkillOutcome`, `emit`, `parse_params`, …), `contract/faults.py` (kinds, `SKILL_FAULTS`/`ROBOT_FAULTS`, fault-file helpers), `contract/stub.py` (stub world format and defaults, `Condition`), `contract.backend.select(table)`/`check()` (`backend_name` is private `_name`); `services/backend/` (`stub_state.py`, `dds.py`; doc `services/backend/docs/backend.md`); `services/robot/backends/__init__.py` (`SportClient`, `RobotBackend`, `ArmHook`, `NoArmHook`, cached `load()`), `backends/faults.py` (`FaultySportClient`, only built by `SimBackend.motion()`); `services/robot/stub.py` (`reset_stub`, the dispatcher's only stub access; kept out of `client.py` so skills never reach the stub world); `skills/faults.py`.
- **Deleted**: `services/robot/local.py`, `services/robot/faults.py`, `services/robot/stub_state.py` (now `services/backend/stub_state.py`).
- `Motion(client, wait, store, arm_hook)`: `arm_hook.clear()` runs before the old lease ends, `arm_hook.arm()` after (so the re-arm StopMove is not hit by the old sticky `stop_error` and a new fault cannot reach the old job's Moves). Real backend: `NoArmHook`.
- `services.robot.protocol` holds `Utility(module, policy)` descriptors `STOP_MOVE`, `READ_STATE`; the utilities take `POLICY` from them; the dispatcher imports no utility module.
- `skills/` now holds `runner.py` (only `run_skill` and its sampler), `faults.py`, `motion.py`, the five skill modules and `catalog/`; there is **no `skills/process.py`** (process helpers are `contract/process.py`). Step 4's `skills/runtime/` should take `runner.py`, `faults.py` and `motion.py`.
- `dispatcher/config.py` imports only `contract.backend`, `contract.faults`, `contract.stub` and `services.vision.coco` (`COCO_CLASSES`): the COCO list is the one remaining implementation-package import for step 6.
- **Open, not fixed (stub only)**: an old action that took `Motion`'s lock before a re-arm's abort, but has not yet reached `FaultySportClient`'s take, could consume the new lease's fault (very small window; the fault was bound per job before). Fix only if a fault test flakes.
- Root `CLAUDE.md` "Working pattern" agent list still says `robot-dev: anything under robot/` and does not name `services/backend/`; it is not main-owned in `.claude/ownership.json`, so it needs the user or a claude-config decision.
- Unverified on the robot (moved, not exercised): `RealBackend` wiring (`start_source`, `motion`, `sample_state`, `sleep`), `services/backend/dds.py`, the real detector's DDS import.

### Step 4 (`refactor-04-skill-declarations.md`): done

On `architecture_refactor`, 5 commits from `manifest: add the uses_vision and moves declarations …` to `docs: name the skill runtime and the per-skill folders in the docs index`. Registry hash `768d05c6633b8fdc` before and after, goldens unchanged; 602 passed, 1 skipped; 97 integration; 15 import contracts kept (new: "The dispatcher imports no skill"); no exceptions.

Notes for later steps:

- **User decisions**: frontmatter `uses_vision: true` (detect_object) and `moves: true` (walk, turn, sit, stretch), `Literal[True]`, absent = false, unrendered. The registry (`dispatcher/registry.py` `_load_skill`) checks them against the code: `uses_vision` iff the entry module or a submodule loaded by importing it holds `services.vision.client` as a global (identity, `_imports_vision`); a non-zero `POLICY.cost` requires `moves`. `SkillDescriptor.uses_vision` / `.moves` are plain bools. `needs_vision(skill)` in `executor.py` is just `skill.uses_vision` (step 6 can inline it at its two callers).
- **Report (user decision, run log unchanged)**: `MOTION_SKILLS` / `STATIONARY_SKILLS` stay in `dispatcher/report.py` as a documented default list; `dispatcher/tests/unit/test_report.py` checks them against the registry's `moves`. When step 6 moves the report to `analysis/`, that check needs the registry, which `analysis` must not import: put it in root `tests/` (cross-package) rather than adding an analysis→dispatcher edge.
- **Layout (user decision)**: `skills/runtime/{runner,faults,motion}.py`; one folder per skill `skills/<name>/{SKILL.md, skill.py, __init__.py, __main__.py}`; `__init__` re-exports `POLICY, body, main` from `skill.py`; `__main__.py` calls `main()`; entrypoint `skills.<name>` (never `python -m skills.<name>.skill`: it would load skill.py twice). `skills.dir` default is `skills`; the registry skips folders without `SKILL.md`. SKILL.md files stay out of the wheel (`skills/*/SKILL.md` exclude). `skills.runtime` (skill side) is distinct from `contract.runtime` (generic process runtime); `skills/docs/skills.md` says which is which.
- **Tests**: `BUILTIN_SKILLS_DIR` (`dispatcher/tests/helpers`) is the one path to the built-in set. A differently named skill set (`peek` uses vision, `spin` moves) is `tests/integration/alt_skills/<name>/`, run end to end in `test_end_to_end.py`. The `env_dump` test skill moved to `tests/integration/env_dump.py` so `dispatcher` imports no `skills` without an exception.
- **Roadmap** gained two open items: the system text names `walk`/`turn` (hash-locked), and the user's future change "SKILL.md LLM-facing only, system data in a separate metadata file per skill" (recorded, not designed; not part of this series unless the user schedules it).
- `.claude/settings.json` ask rule is `Edit(//**/skills/*/SKILL.md)`; it also matches `.claude/skills/*/SKILL.md` (accepted, documented in `.claude/hooks/README.md`). Settings changes apply from a new session.
- Skill names left in `dispatcher/` outside tests: `report.py` default lists, the system text and `EMPTY_TASK` in `prompts.py` (fixed texts), "turn rate" display text in `telegram_progress.py`.
- One unidentified single failure in a full run while two agents edited the tree at once; the rerun and every later run passed. If it recurs, check the known flaky `test_vision_ipc.py::test_result_and_error_round_trip` first.

### Step 5 (`refactor-05-planning-llm.md`): done

On `architecture_refactor`, 8 commits from `tooling: drop the ask rule on dispatcher/prompts.py edits` to `docs: record the planning and llm sub-packages and the text split in architecture.md`. Registry hash `768d05c6633b8fdc` before and after, goldens unchanged; 603 passed, 1 skipped; 97 integration; 18 import contracts kept, no exceptions; basedpyright 13 errors (baseline).

Notes for later steps:

- **User decisions**: `PromptSurface` is data in `dispatcher/llm/surface.py`, built by `context.build_prompt_surface(registry, horizon)`; planners read `surface.tool_schema["name"]`, so `llm` never imports `planning`. `PlanStatus` Literal in `planning/plan.py` is the single source; the schema enum is `list(get_args(PlanStatus))`, the rest of the schema stays a hand-written dict. Texts split by audience: `prompts.py` (model texts only, keeps the E501 ignore, imports nothing from `models`), `operator_messages.py`, `transports/texts.py`; `fault_note` is in `context.py`. The fixed-texts golden test is `dispatcher/tests/unit/test_fixed_texts.py` and walks the three modules. The user removed the `.claude/settings.json` ask rule on `dispatcher/prompts.py` edits (the SKILL.md ask stays); this takes effect from a new session.
- **Layout**: `dispatcher/planning/{plan,schema,validation,bounds,budget,verification}.py`; `dispatcher/llm/{client,retry,surface,anthropic_planner,openrouter_planner}.py` (`retry` names are public: `call_with_retries`, `Failure`, …). `check_reply(tool_input, *, cut_off, horizon)`; `LLMResult.cut_off` is a property (not in `model_dump`). `MOTION_BUDGET_MESSAGE` is defined in `prompts.py`. `LLMUnavailable`/`LLMInterrupted` are in `llm/client.py`. Both sub-package `__init__.py` files are empty (a test checks the CLI import does not load `anthropic`). Tests stay flat in `dispatcher/tests/unit/`.
- **`models.py` keeps for step 6**: the shared pydantic base `Model` (public; `planning/plan.py` imports it so `models` never imports `planning`), the startup/state/busy exceptions, `StepOutcome`, `StopCause`, `StepKill`, `StopMoveResult`, `StepDispatch`, `StepRef`, `Verdict`, `Fault`, `StepResult`, `TaskOutcome*`, `TaskSummary`, `StartState`, `PreconditionFailure`, the `contract.schema` re-exports. It still imports `prompts` (`KILL_*`).
- **Constraint for step 6 (new import contracts)**: `planning` imports `models.{Model, StepRef, StepResult, Verdict, Fault}` (bounds, verification) and `llm` imports `models.StopCause`. Contracts forbid `planning` → `dispatcher.{executor,robot,vision,runlog,context,transports,dispatcher}` and `llm` → those plus `planning`, `registry`, indirect imports included. So result types that `planning` or `llm` use cannot move into `dispatcher/execution/` (or anything importing it); keep them in a module both may import (e.g. `models.py` or a small `results.py`), or the contracts need a user decision. Contract names are in `pyproject.toml` and `tests/docs/testing.md`.
- Remaining `TYPE_CHECKING` imports: SDK types in the two planner modules (lazy imports); annotation-only imports in `runlog.py`, `executor.py`, `robot.py`, `transports/{console_trace,telegram_progress,telegram_text}.py`; none hides a cycle.
- Root `CLAUDE.md` line "Fixed texts in `prompts.py` and `SKILL.md` files change the golden files" is still accurate but does not name `operator_messages.py` (also golden-covered); root `CLAUDE.md` is not in the ownership map, so it needs the user. `basedpyright` baseline error in `tests/integration/test_live_llm.py` (`initial_posture=` no longer accepted by `Dispatcher`) predates the refactor; a live test, not run.
- Process: one agent was interrupted from the UI mid-task and could not be resumed (the harness cancels a stopped subagent); a fresh agent briefed to finish from the partial working tree worked.

### Step 6 (`refactor-06-execution-app.md`): done

On `architecture_refactor`, 7 commits from `docs: name all three golden-covered fixed-text modules in root CLAUDE.md and map it under main` to `analysis: move the run-log report into a top-level analysis package that only the CLI imports`. Registry hash `768d05c6633b8fdc` before and after, goldens unchanged; 615 passed, 1 skipped; 97 integration; 21 import contracts kept, no exceptions; whole-repo basedpyright 12 errors (baseline 13; the `test_live_llm.py` `initial_posture=` error is fixed).

Notes for later steps:

- **Root `CLAUDE.md` is main-owned now** (`main` in `.claude/ownership.json`); its fixed-texts line names `prompts.py`, `operator_messages.py`, `transports/texts.py`; its agent list names `services/robot/` + `services/backend/` for robot-dev and the new `runs-analyzer`. The step 3 and step 5 notes about root `CLAUDE.md` are resolved.
- **New agent `runs-analyzer`** (user decision; overrides "dispatcher-dev owns `analysis/`" below) owns `analysis/`. `dispatcher-dev` keeps the log format and metric definitions (`dispatcher/docs/run-log.md`). A new agent definition loads only in a new session: in the session that creates it, the user approved a temporary ownership grant to dispatcher-dev, reverted before the commit. Plan for this whenever a step adds an agent.
- **`dispatcher/execution/`** (user decisions): `supervisor.py` (`ServiceSupervisor`, `ServiceSpec`), `robot_service.py` (`RobotSupervisor`), `vision_service.py` (`VisionSupervisor`), `state_stream.py`, `env.py` (`ChildEnv`), `process.py` (`SkillProcess`, `parse_response`, `run_utility`), `kill.py` (`KillSwitch`, `Kill`), `control.py` (`ControlLink`, `ServiceLost`, `STOP_WAIT_S`, `LIVENESS_PERIOD_S`), `stop_path.py` (`StopPath`), `outcome.py` (`step_outcome` → `StepVerdict`), `executor.py` (thin `Executor` receiving its parts); `__init__.py` holds `SkillExecutor` and `StreamHandle` (protocol unchanged). `dispatcher/robot.py`, `vision.py`, `executor.py`, `report.py` are deleted. Doc: `dispatcher/docs/execution.md`. `READY_LINE`/`STOP_GRACE_S` stay in each `services/*/protocol.py` (user decision: no shared lifecycle module).
- **Result types**: `service_lost` is stored only on `StopMoveResult`; `ExecResult.service_lost` is a property. `StepResult` holds `process: ProcessRun | None` and a wrap `model_serializer` flattens it to the old `step_result` keys and order (pinned by `dispatcher/tests/unit/test_step_result_dump.py`). `ProcessRun`, `ExecResult`, `ExecOutcome`, `StopMoveResult` are in `models.py` (planning/llm contracts). **Step 7: the run-log writer must keep calling the same serialisation (or replicate the flattening) so `step_result` stays identical.**
- **`dispatcher/app.py`** is the composition root: `build_dispatcher`, `make_planner`, `build_executor`, `load_prompt_surface`, `read_robot_state` (`go2 state`, no executor), `reset_stub`, `STOP_MOVE_TIMEOUT_S`, `TEST_PLANNER_ENV`, `MISSING_API_KEY`. `transports/__init__.py` keeps only `startup_error_text` (fixed text, golden). **Step 7: inject `RunLogFactory` from `app.build_dispatcher`.**
- **COCO list** is `contract/vision.py` (`COCO_CLASSES`); `services/vision/coco.py` is deleted; the vision client surface is `protocol` and `client`.
- **New import contracts**: "Only the composition root, execution and the registry import the services" (source list names every other top-level dispatcher module, `dispatcher.runlog` included; **a new top-level dispatcher module or package must be added to it**); "The analysis package reads logs only" (`allow_indirect_imports = true`, because `dispatcher.runlog` imports `dispatcher.context` and `dispatcher.llm` under `TYPE_CHECKING`; step 7 can tighten it once `runlog/` has no such imports); "Only the CLI imports analysis" (`protected`, allowed `dispatcher.transports.cli`, `tests`). `dispatcher.registry` imports `services.vision.client` for the `uses_vision` check (client surface, accepted).
- **`analysis/`**: `logs.py` (`Task`, `Call`, `Skip`, `read_task`, `read_index`, `task_files`, `file_start`, `NoLogs`), `report.py` (`build`, `render`, `parse_bound`, `Report`, `STUDY_FOLDER`, `MOTION_SKILLS`, `STATIONARY_SKILLS`, `SkillSets`); it imports `dispatcher.runlog` (`FILE_TIME_FORMAT`, `INDEX_FILE`, `STREAM_SUFFIX`, `TOKEN_KEYS`) and `dispatcher.config.Provider`. Tests: `analysis/tests/unit/test_report.py` (build/render), `dispatcher/tests/unit/test_report.py` (the `go2 report` CLI cases; `analysis/tests` may not import the CLI), `tests/test_report_skill_sets.py` (registry cross-check). Doc: `analysis/docs/report.md`; `run-log.md` and `running.md` keep "Latency report" headings with a pointer.
- **Roadmap item added** (user decision): close the robot service explicitly when the control connection fails at start-up instead of relying on its orphan watchdog; today `Executor.start_robot` does not close on `open()` failure. Owner dispatcher-dev; a separate fix with a test.
- **Not done**: the step's "compare a stub run's log records before and after by key set" was covered by the serializer test plus the reviewer's old-vs-new `model_dump` comparison, not by diffing a real integration run's log folder. Step 7's task 1 reference log is the first real-run capture.
- `duration_ms` boundary: measured after the pipe readers join and before the reply is parsed (`SkillProcess.join()` then `finish()`); keep it so.

## What this is

The system works and is tested. The refactor changes its **structure**, not its behaviour: the code has outgrown the "two packages, `dispatcher/` and `skills/`" layout. An architecture review found nine faults (below). The series fixes them in an order where each step leaves the repo green, committed and usable, so the series can stop after any step.

## The faults being fixed

File and line references were correct before step 1. Earlier steps move code, so **find things by symbol name, not by line number**.

1. **`skills/` is a flat folder holding five different things**: the dispatcher↔skill contract, the skill runtime, the five skills, the robot service with its protocol and backends, and the vision service. Only about 5 of its 27 modules are skills.
   - The "Skill processes never touch DDS" import contract in `pyproject.toml` lists skill modules by name, so a new skill is not covered until someone adds it.
   - Shared constants live in whichever module came first. `YAW_SIGN` sits in `skills/robot_ipc.py`, so the SKILL.md parser (`skills/manifest.py`) and `dispatcher/verification.py` import the IPC client for a sign table. Detector errors and `DetectResult` live in `skills/backend.py`.
2. **Stub fault injection is woven into the safety-critical motion code.** `Motion` in `skills/robot_service.py` carries `_stop_error`, `_rejected`, `_fault()`, a `sim` flag and casts to `SimRobot`. The wire protocol passes a `fault` field, and `skills/motion.py` and `skills/detect_object.py` call `stub.inject_fault()` directly.
3. **There is no backend interface.** `if backend_name() == "real"` is repeated in `skills/local.py` (four times), `skills/robot_service.py` (`start_source`, `start_motion`), `backend.sleep` and the vision service.
4. **`dispatcher/executor.py` is a god class.** `Executor` builds the child environment, spawns skills, tracks kills, pings for liveness, runs the stop path and its fallback, manages the control connection, runs utilities, and owns both service supervisors. Methods return `(StopMoveResult, bool)` tuples; `service_lost` exists on both `ExecResult` and `StopMoveResult`; `dispatcher.py` copies `ExecResult` into `StepResult` field by field; `RobotService` (`dispatcher/robot.py`) and `VisionService` (`dispatcher/vision.py`) duplicate about 80 lines of spawn, log and terminate code.
5. **The dispatcher knows skills by name.** `needs_vision` (`executor.py`) compares the entrypoint to `skills.detect_object`; `report.py` hardcodes `MOTION_SKILLS = {"walk","turn","sit","stretch"}`; `verification.py` repeats the `"sdk_error"` string. `skills.dir` is configurable, so these belong to the skill's own declaration.
6. **`dispatcher/llm.py` holds two unrelated jobs**: the plan contract (tool schema, `validate_tool_input`, semantic checks) and two provider clients with the retry loop. The schema's status enum is hand-duplicated from `Plan.status`. `context.py` imports `llm`, and `llm` imports `context` back under `TYPE_CHECKING`, because `PromptSurface` lives in the wrong place.
7. **The run log is a 15-method protocol implemented five times** (`RunLog`, `NullLog`, `TeeLog`, `ConsoleTrace`, `TelegramProgress`). A new record type touches five classes plus the Protocol. `llm_retry` already passes a bare `dict`.
8. **Layering inversions in the dispatcher.** `models.py` imports `prompts`; `config.py` imports `skills.stub` and `skills.coco`; `prompts.py` mixes hash-affecting model texts with operator, Telegram HTML and help texts; the composition root lives in `transports/__init__.py` and reaches through with `executor.vision.start()`; `cli.py` builds a whole `Executor` just to read state.
9. **Task ending in `Dispatcher` is more machinery than it needs.** `_Ending` is unpacked field by field into `_end()`, the phase/stop state machine is interleaved with the loop, and `Dispatcher` builds its own `RunLogFactory`. Lowest priority; the loop itself reads well.

## Target layout

```
contract/    schema, policy, manifest, env vars, error codes, domain constants, shared wire framing
services/    the two processes the dispatcher supervises, plus only what both of them share
  backend/     shared by both services: backend loader, sim world state, DDS init (step 3)
  robot/       service (server, lease/motion, state store), protocol, client,
               backends/{real, sim, faults}, utilities (stop_move, read_state, lease)
  vision/      service, protocol/client, detectors/{real, stub}, coco, box classification
skills/      runtime (runner, process, motion helpers) + one folder per skill (module + SKILL.md)
dispatcher/
  planning/    plan model, tool schema, validation, bounds, budget, verification
  llm/         planner protocol, retry, anthropic, openrouter
  execution/   skill process, stop path, service supervisors (shared base)
  runlog/      events, writer, observers
  transports/  cli, telegram
  app.py       composition root
analysis/    report (it only reads logs)
```

Dependency direction, enforced by import-linter contracts at package level (no module-name lists):

- `contract` imports no other project package. Everything outside `contract.manifest` is standard library only.
- `services.robot` and `services.vision` import `contract` and the shared `services.backend`, never each other, never `skills` or `dispatcher`. Each service keeps its own protocol and client: the two message sets differ, and the common framing is `contract.wire`. Anything skills also import (`contract.backend`, `contract.process`) stays in `contract`.
- `skills` imports `contract` and the `services.robot` / `services.vision` **clients** only: never a backend, a service module, `unitree_sdk2py`, `cyclonedds`, the detector stack, pydantic or yaml.
- `dispatcher` imports `contract`, the clients, and what it needs to start the services (module names, lifecycle constants): never a backend.
- `analysis` imports only what it needs to read logs.

## The steps

| # | Prompt | Result | Faults |
|---|---|---|---|
| 1 | `refactor-01-contract.md` | `contract/` package; no more odd imports for shared constants | 1 (part), 5 (`sdk_error`) |
| 2 | `refactor-02-robot-vision-packages.md` | `robot/` and `vision/` packages, moved as they are; package-level import contracts | 1 |
| 2.5 | `refactor-02.5-services-package.md` | `robot/` and `vision/` moved under `services/`, as they are | 1 |
| 3 | `refactor-03-backend-interface-faults.md` | one backend interface; fault injection out of `Motion` and off the wire | 2, 3 |
| 4 | `refactor-04-skill-declarations.md` | one folder per skill, `skills/runtime/`; skills declare what the dispatcher used to know by name | 1 (rest), 5 |
| 5 | `refactor-05-planning-llm.md` | `dispatcher/planning/` and `dispatcher/llm/`; model texts split from operator texts | 6, 8 (part) |
| 6 | `refactor-06-execution-app.md` | `dispatcher/execution/`, `dispatcher/app.py`, `analysis/` | 4, 8 (rest) |
| 7 | `refactor-07-runlog-events.md` | `dispatcher/runlog/` with typed events and one `emit`; simpler task ending | 7, 9 |

Each step assumes the earlier ones are done. **The repo wins over these files**: at the start of a step, check the actual layout (`ls`, `git log --oneline | head -20`, `docs/architecture.md` "Repository layout"). If an earlier step is missing or ended differently from what the step prompt assumes, tell the user before doing anything.

## Invariants: what must not change in any step

A step that cannot keep one of these stops and asks the user.

- **The registry hash and every golden file** (`dispatcher/tests/golden/`). Capture the hash before you start (`uv run go2 catalog`, last lines) and compare at the end. The hash covers the system text, the catalog text and the tool schema; the `entrypoint` and other unrendered frontmatter are not part of it, which is what lets skill modules move. No fixed text the model sees and no rendered `SKILL.md` content changes.
- **The run log on disk**: file names, envelope, record types and fields, `index.jsonl` (`dispatcher/docs/run-log.md`). Logs written before and after the refactor must be analysable together.
- **The skill response schema, error codes and outcome codes.**
- **The stop path and every other safety behaviour** in `docs/safety.md`: one stop path, the 500 ms fallback, lease semantics, orphan handling, the single-instance lock, timeouts. Moving this code is allowed; changing what it does is not. Treat every diff that touches it as safety-critical and have the reviewer look at it specifically.
- **Config keys, CLI commands and exit codes, Telegram commands and replies.**
- **Offline by default**: the default test suite needs no SDK, API key, network or robot; SDK, CycloneDDS and vision libraries stay lazily imported.
- The robot-service wire protocol stays as it is except where a step prompt says otherwise (step 3 removes the stub-only `fault` field).

Tests are the evidence that behaviour is preserved. Move and re-target tests with the code; do not delete a test because its target moved, and do not weaken an assertion to get green. A deleted test needs a stated reason in the agent's report.

## Rules for every step

Root `CLAUDE.md` applies in full. The points that matter most here:

- **You are the orchestrator.** Delegate every code and doc change to the owning project agent with the `delegate` skill, one task at a time. You edit only the docs listed under `main` in `.claude/ownership.json` (`docs/README.md`, `docs/architecture.md`, `docs/project.md`, `docs/references.md`). Verify with commands only; do not read the whole doc set or source yourself.
- **Safety rules are absolute**: stub backend only; never `--backend real`, never `--run-robot`; no `--run-live` and no `go2 run` (paid API) in this series.
- **One commit per task**, each green: `uv run ruff format .`, `uv run ruff check .`, `uv run lint-imports`, `uv run basedpyright <changed files>`, `uv run pytest -q`, and `uv run pytest -m integration` for anything that touches processes, services or the stop path. Run the `reviewer` agent on the uncommitted diff before each commit, then commit with the `git-commit` skill.
- **Docs move with the code, in the same commit.** A moved module's contract moves to the doc of its new package; code comments cite the new doc path. No doc and code may disagree at any commit.
- **Design decisions are recorded in the owning doc** with the reason and the rejected alternative. The reversal of the "Two services, `dispatcher/` and `skills/`" decision in `docs/architecture.md` is yours to write (step 1 starts it, each later step keeps "Design decisions", "Repository layout" and the component description current). `docs/README.md` tables are also yours.
- **Clean design over small diff.** Do not leave re-export shims, compatibility aliases or "moved to" stubs behind. A rename updates every importer, test, doc and comment.
- **Ask before an architecture decision** that this file or the step prompt does not already settle. Each step prompt lists the decisions it expects; ask them in one question at the start, with your recommendation, then wait.
- **Scope discipline.** A step fixes its own faults. If an agent finds something that belongs to a later step, it reports it and leaves it. If it finds a real bug, it reports it; the fix is a separate commit, with a test, only after the user agrees.

### Decisions already taken for the whole series

Follow these; if one turns out to be wrong, stop and raise it instead of working around it.

- **Every top-level package is laid out like the existing two**: the folder is the Python package, with its own `docs/`, `tests/` and `CLAUDE.md` (commands, doc table, gotchas; links, no `@` paths). Cross-package tests stay in root `tests/`.
- **No new dev agents** (superseded by user decisions: steps 2.5 and 6 added `contract-dev`, `robot-dev`, `vision-dev` and `runs-analyzer`; see "Step status"). `skills-dev` owns `contract/`, `robot/`, `vision/` and `skills/`; `dispatcher-dev` owns `dispatcher/` and `analysis/`. `tests-dev` keeps `pyproject.toml` (packaging, import contracts, type-check and coverage scope) and the test infrastructure. Left out: a separate robot-side agent; the signal to add one is `skills-dev` briefs regularly needing more context than one agent run holds.
- **Whenever a step adds or renames a top-level package**, three things happen before any code moves, each by its owner: `claude-config-dev` updates `.claude/ownership.json` (`agents` paths and `packages`), the agent definitions, and any hook or script that names package paths (for example the safety-file list in `.claude/scripts/review_preview.py`); `tests-dev` updates `pyproject.toml` (`tool.hatch` packages and excludes, `pytest` testpaths, `coverage` source, `importlinter` root packages and contracts, `basedpyright` include); you update `docs/architecture.md` and `docs/README.md`.
- **A contract owned by `contract/` is defined before its users change**: when a task touches several packages, split it per agent and run the defining side first.

## At the end of every step

Report to the user, briefly:

1. Commits made (one line each) and the final results of the full check list above, including the registry hash before and after.
2. Faults closed, and anything in the step's scope that was left open, with the reason.
3. Decisions taken and where they are recorded.
4. Complexity deliberately left out and the signal that would justify adding it.
5. **Notes for the next step**: anything that ended differently from what the next prompt assumes (names, locations, deferred work). The user carries these into the next session.
