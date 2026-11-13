# Step 5 of 7: `dispatcher/planning/` and `dispatcher/llm/`

Read `refactor-00-context.md` first (same branch as this file: `git show origin/refactor_prompts:refactor-00-context.md`). It holds the background, the target layout, the invariants and the working rules for this whole series. Then read root `CLAUDE.md`, `docs/README.md` and `dispatcher/CLAUDE.md`.

## Goal

Split the plan contract from the provider clients, and fix the layering faults that sit between them:

- `dispatcher/planning/`: the plan model, the `submit_plan` tool schema, plan validation (schema and semantic checks), bounds and the whole-plan pre-check, the motion budget, verification.
- `dispatcher/llm/`: the planner protocol, the retry loop, and one module per provider (Anthropic, OpenRouter). It knows how to send a request and return a reply; it does not know what a valid plan is.
- No import cycle between context building and the LLM layer, and data models that do not import text templates.

Everything the model sees stays byte-identical. This step is under the strictest form of the registry-hash invariant: the golden files in `dispatcher/tests/golden/` and the hash printed by `uv run go2 catalog` are the acceptance test.

## Starting point

Only `dispatcher/` changes in this step; steps 1 to 4 reshaped the other packages. Find these by symbol:

- `dispatcher/llm.py` holds both jobs: `plan_tool_schema`, `validate_tool_input`, `_schema_errors`, `_semantic_errors`, `check_reply`, `PlanCheck` (plan contract) and `PlannerClient`, `LLMResult`, `_call_with_retries`, `AnthropicPlanner`, `OpenRouterPlanner` (clients).
- The tool schema's status enum is written by hand next to `Plan.status` in `dispatcher/models.py`. One must be derived from the other, or both from one definition, with a test that the emitted schema is unchanged (`test_tool_schema.py` and the hash).
- `dispatcher/context.py` imports `plan_tool_schema` from `llm`, and `llm` imports `PromptSurface` from `context` under `TYPE_CHECKING`. `PromptSurface` (system text, catalog text, tool schema, registry hash: the static part of every request) is the thing in the wrong place.
- `dispatcher/models.py` imports `dispatcher/prompts.py`: find what the models take from the texts and invert it.
- `dispatcher/prompts.py` mixes three kinds of text: what the model sees (system text, slot texts, notices; these feed the hash and the golden files), operator messages for outcomes and errors, and Telegram HTML and help texts. `pyproject.toml` has a per-file ruff ignore for it because rewrapping changes the hash.
- `dispatcher/bounds.py`, `budget.py`, `verification.py` are planning-side modules that stand alone today.
- `dispatcher/models.py` mixes plan types, step and task result types, and exceptions.

## Design guidance

- **Dependency direction inside `dispatcher/`**: `planning` imports `contract` and the registry, never `llm`, the run log, execution or transports. `llm` imports the prompt surface and config, never `planning`'s validation. The loop (`dispatcher.py`) is the one place that asks a planner for a reply and then asks `planning` to check it. Add import-linter contracts for these directions (`tests-dev` owns `pyproject.toml`).
- **`PromptSurface` goes where both sides can import it downward.** Likely with the model texts and the tool schema, since it is built from the registry and those. The agent proposes; there must be no `TYPE_CHECKING` import that exists only to hide a cycle.
- **Split the texts by audience, not by size**: model-facing fixed texts in one module (the only one with the ruff ignore, and the only one whose edit can change the hash), operator texts in another, Telegram HTML with the Telegram transport. Moving a string must not change one character of it. The fixed-texts golden file is the check.
- **Split `models.py` only as far as the new packages need**: plan types go to `planning`; execution result types can wait for step 6 if moving them now would mean moving them twice. Say in the report what was left for step 6.
- **`registry.py` and `context.py`**: decide whether they belong in `planning/` or stay at the top of `dispatcher/`. The registry is used by planning, context, execution and the app; a top-level module that the sub-packages import is acceptable if it has no upward imports.
- No behaviour change in retries, error classification, request parameters or what counts as an LLM call (`dispatcher/docs/llm.md`).

## Scope

In: the two sub-packages; the status-enum duplication; the `PromptSurface` cycle; `models` no longer importing `prompts`; the text split; tests moved to mirror the packages; `dispatcher/docs/loop-and-context.md` and `llm.md` updated where they name modules; `dispatcher/CLAUDE.md`; comment citations.

Out: `executor.py`, `robot.py`, `vision.py`, the composition root, `config.py`'s imports, `cli.py`, `report.py` (all step 6); the run log (step 7); the loop's task-ending machinery (step 7); any change to a fixed text, the tool schema, validation rules or provider requests.

## Decisions to confirm with the user before delegating

Have `dispatcher-dev` produce, read-only, a module map (old symbol to new module) and the intended import graph between the new sub-packages and the remaining top-level modules. Then ask in one question:

1. The module map, including where `PromptSurface`, `registry.py`, `context.py` and the three text groups go.
2. Which definition of the plan status is the source and how the schema derives from it.
3. How far `models.py` is split now versus in step 6.

## Tasks, in order

1. **You**: check steps 1 to 4 are in place; baseline (suites, `lint-imports`, registry hash; `git status --short dispatcher/tests/golden` clean).
2. **`dispatcher-dev`** (read-only): the module map and import graph. Ask the user.
3. **`dispatcher-dev`**: the text split and the `models` to `prompts` inversion. Golden files untouched. Commit.
4. **`dispatcher-dev`**: `planning/` (plan model, tool schema with the derived enum, validation, bounds, budget, verification) and the `PromptSurface` move. Commit.
5. **`dispatcher-dev`**: `llm/` (protocol, retry, one module per provider). Commit.
6. **`tests-dev`**: import contracts for the new directions; the ruff per-file ignore re-targeted to the model-texts module only. Commit (or folded into the task that makes them pass).
7. **You**: `docs/architecture.md` (repository layout, components) and `docs/README.md` if doc ownership wording names modules.

## Done when

- `dispatcher/llm.py` and the plan-contract code in it are gone; `dispatcher/planning/` and `dispatcher/llm/` exist; nothing under `llm/` imports `planning`'s validation and nothing under `planning/` imports `llm/`.
- `grep -rn "TYPE_CHECKING" dispatcher --include=*.py` shows no import that exists to break a cycle (list any that remain and why).
- The data models import no text module.
- `git diff --stat dispatcher/tests/golden` is empty; the registry hash is unchanged; default and integration suites, `lint-imports`, ruff and basedpyright pass.

Finish with the end-of-step report described in the context file.
