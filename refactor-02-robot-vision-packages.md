# Step 2 of 7: split out the `robot/` and `vision/` packages

Read `refactor-00-context.md` first (same branch as this file: `git show origin/refactor_prompts:refactor-00-context.md`). It holds the background, the target layout, the invariants and the working rules for this whole series. Then read root `CLAUDE.md` and `docs/README.md`.

## Goal

Move the robot service, its protocol, client, backends and utilities into a top-level `robot/` package, and the vision service, its protocol, client and detectors into a top-level `vision/` package. `skills/` is left with the skill runtime and the five skills. The import boundaries become package-level, so a new skill is covered without editing `pyproject.toml`.

This step is a **move with renames only**. No logic changes: `Motion` keeps its fault code, the `backend_name() == "real"` branches stay, the wire protocol is untouched. Step 3 redesigns those; keeping this step mechanical is what makes its diff reviewable for a safety-critical component.

## Starting point

Step 1 created `contract/` (schema, policy, manifest, env, shared wire framing, domain constants). Check that it is there. What is still in `skills/`, by destination:

| Destination | Modules today (find them by name; step 1 may have renamed imports) |
|---|---|
| `robot/` service | `robot_service.py` (server, `Motion`, lease, yaw tracking), `state_store.py` |
| `robot/` protocol and client | `robot_ipc.py` |
| `robot/backends/` | `real.py`, `stub.py` (the sim robot, stub state file, fault injection), `local.py` (the utilities' own robot access), the backend-selection part of `backend.py`, `ramp.py` (start-ramp calibration used by skills and by the sim) |
| `robot/` utilities | `stop_move.py` (the fallback stop), `read_state.py`, `lease.py` |
| `vision/` | `vision_service.py`, `vision_ipc.py`, `real_detector.py`, the stub detector (in `stub.py` today), `coco.py`, `vision.py` (box classification), the detector errors and `DetectResult` from `backend.py` |
| stays in `skills/` | `runner.py`, `process.py`, `motion.py`, `walk.py`, `turn.py`, `sit.py`, `stretch.py`, `detect_object.py`, `catalog/` |

`skills/stub.py` serves both robot and vision (posture, pose and command simulation; scripted detections; fault injection). Splitting it is part of this step: each package gets the part it uses, and what both need (the stub state file, if the detector reads it) has one owner that the other reaches through a small, explicit interface. Do not change what the stub does.

The dispatcher starts these as `python -m <module>`: skills (`dispatcher/executor.py`), the robot service (`dispatcher/robot.py`), the vision service (`dispatcher/vision.py`), and the `stop_move` and `read_state` utilities. Every module string follows the move.

## Scope

In:

- The two packages with the sub-structure of the target layout: `robot/backends/{real, sim}` and `vision/detectors/{real, stub}`. A `faults` module is step 3; fault code moves with the module it lives in today.
- A clear **client surface** per package: the modules a skill or the dispatcher may import (protocol types, the client, lifecycle constants). Everything else in `robot/` and `vision/` is internal.
- Import contracts, all package-level:
  - `skills` may not import `robot` or `vision` except their client modules, nor `unitree_sdk2py`, `cyclonedds`, torch, ultralytics, cv2, pydantic, yaml, `dispatcher`.
  - `robot` and `vision` import `contract` only (not `skills`, not `dispatcher`, not each other beyond what the stub split requires, which must be explicit).
  - `dispatcher` may not import `robot.backends` or `vision.detectors`. Today `dispatcher/config.py` and `dispatcher/transports/` use the stub directly (defaults, `stub.reset`); if a clean import is not possible without redesign, add a named, commented exception to the contract and list it in the report. Step 3 removes it.
  - The detector stack stays importable only by the vision service.
- Docs: `skills/docs/robot.md` moves to `robot/docs/`; the robot-service, stub and fault-injection topics of `skills/docs/skills.md` move to `robot/docs/` and the vision-service topics to `vision/docs/`. One topic, one owner; link instead of copying. `robot/CLAUDE.md`, `vision/CLAUDE.md`, and `skills/CLAUDE.md` trimmed to what stays.
- Tests move with their code (`skills/tests/unit/test_robot_service.py`, `test_stub.py`, `test_real_state.py`, `test_vision_ipc.py`, `skills/tests/robot/`, the fake clock). Root `tests/integration/` keeps its place and updates its imports.

Out: any behaviour change; the backend interface and fault redesign (step 3); reshaping `skills/` into `runtime/` plus one folder per skill (step 4); anything in `dispatcher/` beyond imports, module strings and doc citations.

## Decisions to confirm with the user before delegating

Ask in one question, with the recommendation, then wait. Have `skills-dev` produce the module map first (a read-only task) so the question is concrete.

1. **The module map**: old module to new module, including how `stub.py` and `backend.py` are split and where `ramp.py` and the stub state file go.
2. **The client surface**: which modules of `robot/` and `vision/` skills and the dispatcher may import.
3. **Package names `robot` and `vision`**, plain like the others.

## Tasks, in order

1. **You**: check step 1 is in place; capture the baseline (default suite, integration suite, `lint-imports`, registry hash).
2. **`skills-dev`** (read-only): propose the module map and client surface. Then ask the user.
3. **`claude-config-dev`**: ownership map (`skills-dev` scope, `packages`), agent definitions, and the hooks and scripts that name paths. `.claude/scripts/review_preview.py` has a list of safety files: every moved safety-critical module must still trigger it.
4. **`tests-dev`**: `pyproject.toml` packaging, test paths, coverage, type-check scope; the new import contracts land with or right after the move (keep every commit green).
5. **`skills-dev`**: the `robot/` move (code, tests, docs, `CLAUDE.md`). One commit.
6. **`skills-dev`**: the `vision/` move, the same way. One commit.
7. **`dispatcher-dev`**: imports, `python -m` module strings, lifecycle constants, comment and doc citations under `dispatcher/`.
8. **You**: `docs/architecture.md` (components, process model wording where it names modules, repository layout, design decisions) and `docs/README.md` (folders and tables).

Tasks 5 to 7 may need to land together to stay green; if so, run the agents in sequence and commit once, with the reviewer looking at the whole diff. Tell the reviewer that this diff is supposed to contain **no logic change** in the stop path, the lease, `Motion` or the fallback stop, and ask it to confirm that specifically.

## Done when

- `skills/` contains only the runtime modules, the five skills and `catalog/`.
- The "Skill processes never touch DDS" rule no longer lists modules by name; `uv run lint-imports` passes with package-level contracts.
- `git diff --stat` for the moved files shows renames with import and citation edits only (use `git diff -M` to check).
- Default and integration suites pass; the registry hash is unchanged; `uv run go2 state` and `uv run go2 catalog` work.

Finish with the end-of-step report described in the context file.
