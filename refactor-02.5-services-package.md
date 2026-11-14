# Step 2.5 of the series: move `robot/` and `vision/` under `services/`

Read `refactor-00-context.md` first (same branch as this file: `git show origin/refactor_prompts:refactor-00-context.md`), including the step 1 and step 2 entries in "Step status". It holds the background, the target layout, the invariants and the working rules for this whole series. Then read root `CLAUDE.md` and `docs/README.md`.

This step sits between step 2 and step 3. It was added after step 2 was done.

## Goal

Move the two top-level packages `robot/` and `vision/` into one top-level package, `services/`, as `services/robot/` and `services/vision/`. The top level then shows the system's parts: `contract/`, `services/`, `skills/`, `dispatcher/` (and later `analysis/`).

This is a **pure move with renames**, like step 2. No logic changes, no new shared code: the backend interface and the shared `services/backend/` module are step 3, the shared service lifecycle is a step 6 candidate. Fault code, `backend_name() == "real"` branches, the wire protocol, the service lifecycle constants and the ten import-contract exceptions all stay as they are, only renamed.

## Why

- `robot` and `vision` are the same kind of thing: long-lived processes the dispatcher supervises, each with a protocol, a client, backends and a service. Grouping them makes that visible and gives step 3 one place for what both services share.
- Four of step 2's ten exceptions are vision reaching into robot code (the stub camera reads the sim state; the real detector uses the robot's DDS init). Those are not a vision-to-robot dependency: both services sit on the same simulated world and the same DDS connection. Step 3 moves that shared part to `services/backend/`; this step prepares the place.
- Rejected: a shared protocol or client module in `services/`. The two protocols are different message sets, and the common framing is already `contract.wire`, which skills and the dispatcher also use. Rejected: moving `contract/backend.py` or `contract/process.py` under `services/`; skills import both.

## Starting point

Check the layout step 2 left (its "Step status" entry): `robot/` (service, protocol, client, `backends/{real, sim}`, `faults.py`, `stub_state.py`, `local.py`, `lease.py`, `ramp.py`, `state_store.py`, `stop_move.py`, `read_state.py`, `docs/`, `tests/`, `CLAUDE.md`) and `vision/` (service, protocol, client, `detectors/{real, stub}`, `coco.py`, `boxes.py`, `docs/`, `tests/`, `CLAUDE.md`). If it differs, tell the user before doing anything.

Everything that names these packages by path or module string has to follow the move. Find them all with `git grep -nE "\b(robot|vision)[./]"` and sort out the false positives (`dispatcher/robot.py`, `dispatcher/vision.py`, prose). Known places:

- Imports in `dispatcher/`, `skills/`, `contract/` tests, `tests/integration/` (including the helper modules there that start the services) and the packages' own tests.
- Module strings: `SERVICE_MODULE` in both `protocol.py` files, the utilities' module names the dispatcher runs with `python -m`, any `importlib` or lazy-import string, and the module lists in the fresh-interpreter test in `tests/integration/test_skills.py`.
- `pyproject.toml`: hatch packages and excludes, pytest testpaths, coverage source, `importlinter` root packages and every contract (the forbidden-module lists and the ten "exception, to be removed" entries, whose wording keeps saying so), basedpyright include.
- `.claude/`: `ownership.json` (agent paths, `packages`), agent definitions (the reviewer's check that names the robot service motion loop), `.claude/scripts/review_preview.py` (safety files and `CONTRACTS` paths), `TYPED_ROOTS` in `.claude/hooks/typecheck.py`, any other hook or script that names the paths.
- Docs: every doc link and code-comment citation of `robot/docs/…` and `vision/docs/…`; `docs/roadmap.md` ("Import-contract exceptions" rows); `docs/safety.md` if it names files; `docs/architecture.md` and `docs/README.md` (yours).

## Scope

In: the move; every importer, module string, test, doc, citation and tool configuration listed above; the `services/` package itself and its `CLAUDE.md` (per decision 2).

Out: any behaviour change; new shared modules under `services/` (step 3 for the backend, step 6 for the lifecycle); removing any of the ten exceptions (step 3); reshaping `skills/` (step 4); changes in `dispatcher/` beyond imports, module strings and citations.

## Decisions to confirm with the user before delegating

Ask in one question, with your recommendation, then wait.

1. **Names**: `services`, with `services.robot` and `services.vision` (recommended: keep the subpackage names, they are already in docs and briefs).
2. **What `services/` itself holds**: recommended an empty `__init__.py` and a short `services/CLAUDE.md` (a doc table linking both subpackages' docs, and gotchas that apply to both). No `services/docs/` or `services/tests/` yet; step 3 adds `services/backend/` with its own doc. Each subpackage keeps its own `docs/`, `tests/` and `CLAUDE.md`. Alternative: no `services/CLAUDE.md` until step 3.
3. **Import contracts**: `root_packages` lists `services` instead of `robot` and `vision`; the existing contracts are kept with `services.robot.…` / `services.vision.…` names (the "Robot imports neither skills, the dispatcher nor vision" pair stays two forbidden contracts, because each also forbids `skills` and `dispatcher`). Alternative: an `independence` contract between the two plus one forbidden contract on `services`; rejected for now because the ten exceptions are written per contract and step 3 rewrites them anyway.

## Tasks, in order

1. **You**: check step 2 is in place; baseline (default suite, integration suite, `lint-imports`, registry hash, `git status --short dispatcher/tests/golden` clean).
2. **`claude-config-dev`**: ownership map, agent definitions, hooks and scripts. Committed alone if it can be green on its own (the step 1 note on the order when adding a package applies: an exemption for the not-yet-existing folder, `TYPED_ROOTS` after `pyproject.toml`). The moved safety files must still trigger the reviewer's safety check.
3. Then, uncommitted and in sequence, as one green commit (no shims, so the rename cannot be green in between): **`tests-dev`** `pyproject.toml`; **`skills-dev`** the move with `git mv` (code, tests, docs, the package `CLAUDE.md` files, `services/CLAUDE.md`, `skills/` and `contract/` importers, `docs/roadmap.md` rows, `docs/safety.md` if it names files); **`dispatcher-dev`** imports, module strings and citations under `dispatcher/`; **`tests-dev`** `tests/integration/` and `tests/helpers/`; **`claude-config-dev`** follow-up (remove the exemption, `TYPED_ROOTS`, `CONTRACTS` paths). Brief the first agent that the suite fails on imports until the last one runs, and ask it for the old-to-new path list for the next briefs.
4. **You**: `docs/architecture.md` (repository layout, components, the design decision for `services/` with the reason and the rejected alternatives from "Why" above, in the present tense) and `docs/README.md` (folders and doc tables). Include them in the same commit as task 3 so docs and code never disagree.

Tell the reviewer that the diff is supposed to contain **no logic change**, especially in the stop path, the lease, `Motion` and the fallback stop, and ask it to confirm that with `git diff -M --stat` and by reading the non-rename hunks.

## Done when

- No top-level `robot/` or `vision/` remains; `services/robot/` and `services/vision/` hold everything they held.
- `git grep -nE "\b(robot|vision)[./]"` finds every path and module name with the `services` prefix; anything else in the list is a false positive you can name.
- `git diff -M --stat` shows renames with import, string and citation edits only.
- `uv run lint-imports` passes with the same ten exceptions, renamed; default and integration suites pass; the registry hash and golden files are unchanged; `uv run go2 state` and `uv run go2 catalog` work.

Finish with the end-of-step report described in the context file. Then add a "Step 2.5" entry to "Step status" in `refactor-00-context.md` on `refactor_prompts`, as that file says.
