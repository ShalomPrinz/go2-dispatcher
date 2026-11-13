# Step 4 of 7: skills declare themselves; `skills/` becomes runtime plus one folder per skill

Read `refactor-00-context.md` first (same branch as this file: `git show origin/refactor_prompts:refactor-00-context.md`). It holds the background, the target layout, the invariants and the working rules for this whole series. Then read root `CLAUDE.md` and `docs/README.md`.

## Goal

1. **The dispatcher no longer knows any skill by name.** What it needs to know about a skill (does it use the vision service, does it move the robot) is declared by the skill and read through the registry. `skills.dir` is configurable, so a different skill set must work without editing the dispatcher.
2. **`skills/` has its final shape**: a `runtime` sub-package (runner, process, motion helpers) and one folder per skill holding its module and its `SKILL.md`. Adding a skill is adding one folder.

## Starting point

After steps 1 to 3: `contract/` holds the manifest schema (`SkillFrontmatter`, `Expectation`, `Precondition`), `SkillPolicy` and `MotionCost`; `robot/` and `vision/` exist with client surfaces; `skills/` holds `runner.py`, `process.py`, `motion.py`, the five skill modules and `catalog/<name>/SKILL.md`.

The name checks to remove (find by symbol):

- `needs_vision` in `dispatcher/executor.py` compares the skill's entrypoint to the `detect_object` module; it is used when running a step and at start-up (`dispatcher/transports/__init__.py`) to decide whether to start the vision service.
- `MOTION_SKILLS = {"walk", "turn", "sit", "stretch"}` in `dispatcher/report.py`, used for time-to-first-motion and motion step counts. The report reads logs, not the registry.
- Anything else of the same kind: search `dispatcher/` (outside tests) for the five skill names and for `skills.` module references, and list what you find for the user before changing it. A skill declared `self_reported` already exists as a declaration; use it as the model.

How skills load today: `dispatcher/registry.py` reads each `<skills.dir>/<name>/SKILL.md`, imports the `entrypoint` module, and takes its module-level `POLICY`. The executor starts a skill as `python -m <entrypoint> '<json params>'`. The catalog text renders name, description, params and body sections only, so frontmatter that is not rendered (like `expect` and `requires`) does not affect the registry hash.

## Design guidance

- **Where a declaration goes.** `SKILL.md` frontmatter is the skill's description of itself (what it needs, what it does); `POLICY` is the dispatcher policy computed from params (timeout, cost). "Uses the vision service" and "commands motion" are descriptions, so frontmatter is the default home. Motion may be better derived than declared: a skill that requests a lease, or whose `MotionCost` is non-zero, moves. Sit and stretch have zero distance and rotation but do move, so check what `MOTION_SKILLS` actually means for the study metric before choosing.
- **The report must not need the registry.** If the run log already records what the report needs per step, derive from that. If it does not, the options are a new field on a record (a log-format change: an invariant in the context file, so it needs the user's explicit decision and a note in the run-log doc on how old logs are read) or keeping a documented default list in the report. Present the options; do not choose a log change silently.
- **Declarations are validated when the registry loads**, like `expect` and `requires`: a skill that declares vision use but is not a vision client, or the reverse, should fail fast where that can be checked cheaply; do not build a capability framework.
- **Folder per skill.** The folder must work with all three mechanisms at once: registry discovery by `SKILL.md`, `importlib.import_module(entrypoint)` finding `POLICY`, and `python -m <entrypoint>`. Recommended: `skills/<name>/SKILL.md` plus the skill code in the same folder, with `skills.dir` pointing at `skills/` and the registry skipping folders without a `SKILL.md` (it already does). The agent proposes the exact file layout and entrypoint strings; the entrypoint is not rendered, so the hash does not change.
- **`skills.dir` stays a config key with the same meaning** (a folder of skill folders). Its default changes if `catalog/` goes away: update `docs/configuration.md`, `config.example.toml` and the running doc's "switching skill sets" section.

## Scope

In: the declarations and their validation in `contract/` and the registry; removing the name checks; `skills/runtime/`; one folder per skill; the "how to add a skill" doc; `skills/CLAUDE.md`; the import contract for `skills` re-checked (still package-level, the runtime included).

Out: splitting `Executor` (step 6 moves `needs_vision`'s callers; here only its body changes); moving `report.py` to `analysis/` (step 6); changes to any rendered `SKILL.md` text, param, or to skill behaviour.

## Decisions to confirm with the user before delegating

Ask in one question, with recommendations, after a read-only look by the agents:

1. The declaration for vision use: its name and place (frontmatter recommended).
2. How "motion skill" is determined for the report, and whether that needs a log change (see above).
3. The skill folder layout, the entrypoint strings, and the new `skills.dir` default.
4. The list of any further name checks found, and what replaces each.

## Tasks, in order

1. **You**: check steps 1 to 3 are in place; baseline (suites, `lint-imports`, registry hash, golden files clean).
2. **`skills-dev`** and **`dispatcher-dev`** (read-only, one after the other): the proposal for the decisions above. Ask the user.
3. **`skills-dev`**: declarations in the manifest schema and in the five `SKILL.md` files, with manifest tests and the contract doc. Commit.
4. **`dispatcher-dev`**: registry exposes the declarations; `needs_vision` and the other name checks use them; tests use a skill set with different names to prove no name is assumed. Commit.
5. **`skills-dev`**: `skills/runtime/` and one folder per skill; docs. **`dispatcher-dev`**: `skills.dir` default, config and running docs, any path in tests. **`tests-dev`**: packaging excludes, golden or helper paths, import contracts. Sequence them so each commit is green.
6. **You**: `docs/architecture.md` (the "Skill manifests live in `skills/catalog/<name>/SKILL.md`" decision is replaced; repository layout; glossary if it names the catalog folder) and `docs/README.md`.

## Done when

- `grep -rnE "\b(walk|turn|sit|stretch|detect_object)\b" dispatcher --include=*.py` finds no behaviour-affecting use outside tests (operator-text examples and help strings are fine; list what remains in the report).
- A test loads a skill set whose names differ from the built-in five and runs a vision skill and a motion skill through the dispatcher on the stub.
- `skills/` contains `runtime/`, five skill folders, `docs/`, `tests/`, `CLAUDE.md`.
- `uv run go2 catalog` prints the same catalog and registry hash as before; golden files unchanged; default and integration suites pass.

Finish with the end-of-step report described in the context file.
