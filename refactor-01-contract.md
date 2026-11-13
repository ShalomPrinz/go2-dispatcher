# Step 1 of 7: extract the `contract/` package

Read `refactor-00-context.md` first (same branch as this file: `git show origin/refactor_prompts:refactor-00-context.md`). It holds the background, the target layout, the invariants and the working rules for this whole series. Then read root `CLAUDE.md` and `docs/README.md`.

## Goal

Create a top-level `contract/` package that holds everything the dispatcher, the skills and the services agree on, and that imports no other project package. After this step nobody imports an IPC client, a backend or the stub to get a shared constant or type.

This step is mechanical: modules and names move, behaviour does not change.

## Starting point

`skills/` is one flat package. The contract parts today:

| Today | What it is |
|---|---|
| `skills/schema.py` | skill response and robot state schema, error codes (`SkillError`), posture |
| `skills/policy.py` | `SkillPolicy`, `MotionCost` |
| `skills/manifest.py` | SKILL.md schema and parser (pydantic and yaml; dispatcher process only) |
| `skills/env.py` | the `GO2_*` environment contract and `child_env`, socket and lock paths |
| `skills/ipc.py` | JSON framing shared by the robot and vision protocols |
| `YAW_SIGN` in `skills/robot_ipc.py` | turn direction to yaw sign; imported by `manifest.py`, `turn.py`, `dispatcher/verification.py` |
| `"sdk_error"` in `dispatcher/verification.py` (`REJECTED_ERROR_CODES`) | a string copy of `SkillError.SDK_ERROR` |

## Scope

In:

- Move the modules above into `contract/`. Module names inside it are the agent's to propose (for example `contract/wire.py` for the shared framing); keep one clear purpose per module.
- Give domain constants a home in `contract/` (a small constants module): `YAW_SIGN` at least. Add any other constant that two packages share and that today sits in a service, client or stub module. The test is: "does a second package import this module only for this value?"
- `dispatcher/verification.py` uses the error-code enum from `contract`, not a string literal.
- Types and defaults that `dispatcher/config.py` takes from `skills/stub.py` (`StubPosture`, `FaultKind`, the stub defaults, `parse_objects`): move the **types and constants** that are contract-like into `contract/` if that removes the import of the stub implementation cleanly. If it does not come apart cleanly, leave it for step 2 and say so in the report. `skills.coco` stays where it is until step 2.
- Docs: a `contract/docs/` doc (suggested name `contract.md`) takes over the topics that move from `skills/docs/skills.md`: SKILL.md format, invocation and environment, the response schema, error codes, policies. `skills.md` keeps what stays in `skills/`. Every comment that cites a moved topic cites the new doc.
- `contract/CLAUDE.md`, `contract/tests/` (the moved unit tests, for example the manifest tests).
- Import contracts in `pyproject.toml`: `contract` imports no project package; only `contract.manifest` may import pydantic and yaml; `skills` still may not import `contract.manifest` (keep the existing "only the dispatcher and tests import the SKILL.md parser" rule, re-targeted).

Out (later steps): moving the robot service, IPC clients, backends, stub, vision, utilities or skills; any change to `Motion`, fault injection or backend selection; anything inside `dispatcher/` beyond updating imports and the `sdk_error` reference.

## Decisions to confirm with the user before delegating

Ask these in one question, with the recommendation, then wait.

1. **Package name `contract`** (as in the target layout). Alternative: `go2_contract`, if a clash with an installed package worries the user; the existing decision in `docs/architecture.md` accepted plain names for `dispatcher` and `skills`.
2. **`contract.manifest` keeps pydantic and yaml**, as the one exception to "standard library only", guarded by an import contract. Alternative: put the parser in `dispatcher/` and keep only stdlib types in `contract/`; rejected by default because skill tests also validate SKILL.md files and the manifest schema is the contract.
3. **Doc split** as described above (a new `contract/docs/contract.md` taking sections from `skills.md`).

## Tasks, in order

1. **You**: capture the baseline (`uv run pytest -q 2>&1 | tail -n 5`, `uv run lint-imports`, `uv run go2 catalog | tail -n 3` for the registry hash).
2. **`claude-config-dev`**: add `contract/` to `.claude/ownership.json` (`skills-dev` scope and `packages`), the agent definitions, and any hook or script that names package paths.
3. **`tests-dev`**: `pyproject.toml` for the new package (wheel packages and excludes, testpaths, coverage source, import-linter root packages, basedpyright include) and the new import contracts. The contracts can only pass once the code has moved, so run this together with task 4 in sequence and commit when both are green, or have `tests-dev` land the packaging first and the contracts after task 4. Choose the order that keeps every commit green.
4. **`skills-dev`**: create `contract/`, move the modules and constants, update every importer under `skills/`, move the tests and the doc sections, write `contract/CLAUDE.md`. Stops and reports anything it needs changed under `dispatcher/`.
5. **`dispatcher-dev`**: update `dispatcher/` imports, replace the `sdk_error` literal, update citations in dispatcher comments and docs.
6. **You**: `docs/architecture.md` (replace the "Two services" decision with the new package structure and its reason, record the rejected alternative of keeping two packages; update "Repository layout" and the component text to the state after this step, not the final target) and `docs/README.md` (folder list, a Contract table).

Reviewer before each commit; `git-commit` skill for each commit.

## Done when

- `contract/` exists with docs, tests and `CLAUDE.md`; nothing in it imports `skills`, `dispatcher` or a third-party package outside `contract.manifest`, and `uv run lint-imports` proves it.
- `grep -rn "robot_ipc import YAW_SIGN\|\"sdk_error\"" dispatcher skills --include=*.py` finds nothing outside tests and the operator-text table in `prompts.py`.
- No re-export shims are left in `skills/` for the moved names.
- The full check list in the context file passes, and the registry hash is unchanged.

Finish with the end-of-step report described in the context file.
