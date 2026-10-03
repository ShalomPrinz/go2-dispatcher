# Claude Code hooks

Wired in `.claude/settings.json`; each runs as `python3 .../<hook>.py` with a 120 s timeout. Lint, format, type-check and import-contract rules live in [tests/docs/testing.md](../../tests/docs/testing.md).

| File | Event | Blocks? | What it does |
|---|---|---|---|
| `hooklib.py` | — | — | Shared plumbing, stdlib only: stdin payload, `stop_hook_active` guard, tree, changed files, status line, loop breaker. |
| `lint.py` | Stop, SubagentStop | Yes (exit 2) | Applies `ruff format` and import sorting (`ruff check --select I --fix`) to the changed `.py` files, then runs `ruff check` on them. Status `format ✓ (N files) · lint ✓ (N files)`, or `format - · lint -` with no changes. |
| `typecheck.py` | Stop, SubagentStop | Yes (exit 2) | Runs `basedpyright` on the changed `.py` files under `dispatcher/`, `skills/` and `tests/`, and `lint-imports` on the whole project, in parallel. Status `types ✓ (N files) · imports ✓`, or `types -` with no changes. |

On a block the failing tool's output goes to stderr, which Claude reads, and the turn continues.

The type check needs the optional extras installed (`uv sync --extra robot --extra vision`); without them the robot and vision imports in `skills/real.py` are unresolved and touching that file blocks.

## Why they are built this way

- **Changed files only** (`git diff HEAD` plus untracked, existing `.py` files): fast, and an agent is never blocked by errors in files it did not touch. No baseline file is needed, because the commit rule `uv run ruff format .` keeps committed files clean. The whole-repo checks stay the pre-commit commands. A touched file that already had type errors does block; the loop breaker lets the second stop through.
- **Type check limited to the `[tool.basedpyright]` `include` roots** (`dispatcher/`, `skills/`, `tests/`, listed in `typecheck.py`). File arguments bypass `include`, so without the filter a root-level script would be checked although the config leaves it out. `lint-imports` is whole-project and fast, so it runs whenever any `.py` file changed.
- **Format never blocks, and runs inside `lint.py`, before the lint.** Hooks on one event run in parallel; a separate format hook would race the lint on the same files, and the `W`, `E501` and `I` rules would fail falsely on unformatted code. The format status is merged into the lint line.
- **`typecheck.py` runs in parallel with `lint.py` safely.** It never writes files, and formatting never changes types.
- **One JSON object on stdout.** `report()` prints `{"systemMessage": ..., "suppressOutput": true}`, the only line the user sees, so every hook reports even when idle (`lint -`, `types -`). A hook must print at most one object.
- **Loop breaker.** With `stop_hook_active` true a hook exits 0 at once. In addition, `block()` stores an md5 of the failure output in `${TMPDIR:-/tmp}/claude-<hook>-<session_id>` (session id reduced to `[A-Za-z0-9_-]`); the same failure a second time reports `<hook> ✗ unchanged since the last block; not blocking again (run <command>)` and exits 0. A clean run deletes the file.
- **Tree from the payload `cwd`** (fallback `git rev-parse --show-toplevel`; exit 0 outside a repo), never `$CLAUDE_PROJECT_DIR`, so a session in a git worktree checks its own tree, not the main checkout.
- **System `python3`, tools through `uv run`.** The hooks are stdlib only, so they skip the venv start-up; ruff, basedpyright and lint-imports still run from the project environment.

## Design decisions

- **Python over bash, on one shared module.** The payload parsing, tree lookup, changed-file list, status line and loop breaker live once in `hooklib.py` instead of being copied into each hook. Rejected: bash scripts with inline `python3 -c` snippets for the JSON handling, which duplicated that plumbing in every hook and needed a second language for it anyway.
