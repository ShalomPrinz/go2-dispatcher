# Step 6 of 7: `dispatcher/execution/`, the composition root, and `analysis/`

Read `refactor-00-context.md` first (same branch as this file: `git show origin/refactor_prompts:refactor-00-context.md`). It holds the background, the target layout, the invariants and the working rules for this whole series. Then read root `CLAUDE.md`, `docs/README.md`, `dispatcher/CLAUDE.md` and `docs/safety.md`.

## Goal

1. **Break up the `Executor` god class** into parts with one job each, inside `dispatcher/execution/`: running a skill process, the stop path with its fallback, and the service supervisors on a shared base.
2. **One composition root**, `dispatcher/app.py`, that builds and wires everything; transports only parse input and present output.
3. **Finish the layering clean-up**: `config.py` imports no implementation module, `go2 state` does not build an executor, and the log report moves to a top-level `analysis/` package.

The executor carries the dispatcher's side of the stop path. Its behaviour must not change: same order of actions, same waits, same fallback, same records. Structure changes only.

## Starting point

Steps 1 to 5 are done: `contract/`, `robot/` (with a backend interface and a client surface), `vision/`, `skills/` (runtime plus skill folders, capabilities declared), `dispatcher/planning/`, `dispatcher/llm/`. Find these by symbol:

- `Executor` in `dispatcher/executor.py` does all of: build the child environment, spawn the skill process group and read its pipes, track kills and their cause, ping the control connection for liveness while a step runs, run the stop path (`stop` on the control connection, wait for `stop_sent`, otherwise SIGKILL the service and run the `stop_move` utility), manage the control connection, run the `read_state` utility, and own the `RobotService` and `VisionService` supervisors. The `SkillExecutor` protocol next to it is what the loop depends on.
- Symptoms to remove: private methods returning `(StopMoveResult, bool)` tuples; `service_lost` on both `ExecResult` and `StopMoveResult`; `dispatcher.py` copying `ExecResult` into `StepResult` field by field.
- `RobotService` (`dispatcher/robot.py`) and `VisionService` (`dispatcher/vision.py`) each implement spawn, log file, readiness wait and terminate; about 80 lines are the same.
- The composition root is `build_dispatcher` and `make_planner` in `dispatcher/transports/__init__.py`. It calls `executor.vision.start()` through the executor, and decides whether vision is needed from the registry.
- `dispatcher/transports/cli.py` builds an `Executor` for the `state` command just to read state once.
- `dispatcher/config.py` imports stub and COCO names from implementation packages (step 3 may already have moved the stub part to a client surface or `contract/`; check what is left).
- `dispatcher/report.py` reads run logs and imports a few constants from the run log module and config.
- Result types that step 5 left in `dispatcher/models.py` for this step.

## Design guidance

- **Parts, not layers.** Suggested split: a skill-process runner (environment, spawn, pipes, kill, response parsing), a stop path object (control-connection stop, the wait, the fallback), a robot link (control connection, liveness, state reads, restart), and the two supervisors. A thin executor composes them and implements `SkillExecutor`. The agent proposes the real cut after reading the code; the test is that each part can be described in one sentence and tested without the others.
- **One home for each fact.** Decide which result type owns "the robot service was lost" and have the other derive or reference it. Replace the tuples with a small result type or by letting the stop path own its state.
- **`ExecResult` to `StepResult`** without a field-by-field copy: compose (the step result holds the execution result) or share a base. Composition is the default. The on-disk `step_result` record must stay identical; if composing changes serialisation, the writer flattens it.
- **A shared supervisor base** for spawn, log, wait-for-ready and terminate, with the robot and vision specifics (lock, control connection, lazy readiness for vision) in subclasses or parameters. Do not generalise past these two services.
- **`app.py`** builds: process lock, registry, stub reset, prompt surface, planner, execution parts, service start, dispatcher, and the vision start. Vision start-up is requested from the supervisor directly, not through the executor. Start-up order and failure handling stay as documented in the running doc (for example the executor is closed if building the dispatcher fails).
- **`go2 state`** uses the smallest thing that reads state (the robot client or the read-state utility), through `app.py` or the execution package.
- **`config.py`** takes shared types, defaults and the COCO class list from `contract/` or from stdlib-only client-surface modules. If the COCO list has to move to make that true, that is a `skills-dev` task done first.
- **`analysis/`** is a top-level package for the report. It may import the run log's file-format constants; it must not import the loop, execution or transports. Check how the report is invoked today (module, CLI subcommand) and keep that working. If the run-log constants it needs are few, consider whether they belong with the log format definition that step 7 will introduce; do not pre-build step 7.
- **Timing constants** (`STOP_WAIT_S`, `LIVENESS_PERIOD_S`, poll and join timeouts) move with their code, values unchanged, still marked `(tunable)` where they are.

## Scope

In: everything above; tests moved to mirror the new parts (the executor integration tests are the regression net: keep every one); `dispatcher/docs/` and `docs/safety.md`, `docs/running.md`, `docs/configuration.md` where they name moved code; `dispatcher/CLAUDE.md`; a small `analysis/` with `CLAUDE.md` and tests; ownership and packaging for `analysis/`.

Out: the run log's protocol and observers (step 7; the execution parts keep calling the observer as they do now); the loop's phase and ending machinery (step 7); any timing, ordering or semantic change on the stop path.

## Decisions to confirm with the user before delegating

Have `dispatcher-dev` produce, read-only: the proposed parts with their one-sentence responsibilities and public methods, the result-type changes, the supervisor base, the `app.py` wiring, and the `analysis/` boundary. Then ask in one question:

1. The cut of `Executor` into parts and the shape of the `SkillExecutor` protocol afterwards (unchanged is preferred, so the loop and its fakes do not move).
2. Where `service_lost` lives and how `StepResult` relates to `ExecResult`.
3. `analysis/` as a top-level package (as in the target layout) and how the report is invoked.

## Tasks, in order

1. **You**: check steps 1 to 5; baseline (default and integration suites, `lint-imports`, registry hash). Record which integration tests cover the stop path, the fallback, service loss and restart.
2. **`dispatcher-dev`** (read-only): the proposal. Ask the user.
3. **`dispatcher-dev`**: the shared supervisor base; `RobotService` and `VisionService` on it. Commit.
4. **`dispatcher-dev`**: `execution/` with the executor split and the result types. Commit. This is the safety-critical diff: tell the reviewer to compare the stop path before and after as a sequence of actions (kill the process group, send `stop`, wait, SIGKILL the service, run the fallback, restart) and to confirm nothing was reordered, dropped or retimed.
5. **`dispatcher-dev`**: `app.py`, transports reduced to input and output, `go2 state` without an executor, `config.py` imports. Commit.
6. **`claude-config-dev`**, **`tests-dev`**, then **`dispatcher-dev`**: `analysis/` (ownership, packaging and import contract, then the move). Commit.
7. **You**: `docs/architecture.md` (components, repository layout) and `docs/README.md` (an Analysis entry if it gets a doc; the run-log doc keeps owning how metrics are computed).

## Done when

- No class in `dispatcher/execution/` both spawns skill processes and supervises services; no method returns a `(result, bool)` tuple; `service_lost` has one owner.
- `dispatcher/transports/__init__.py` holds no wiring; `grep -rn "executor.vision" dispatcher` finds nothing.
- `dispatcher/config.py` imports no backend, stub or detector module; `lint-imports` enforces `dispatcher` to `contract` and client surfaces only, and `analysis` away from the loop.
- Default and integration suites pass with no test deleted; the registry hash is unchanged; a stub run's log files have the same record types and fields as before (compare one integration run's records before and after, by key set).

Finish with the end-of-step report described in the context file.
