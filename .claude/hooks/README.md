# Claude Code hooks

Wired in `.claude/settings.json`. Lint and format rules live in [tests/docs/testing.md](../../tests/docs/testing.md).

| Hook | Event | Blocks? | What it does |
|---|---|---|---|
| `lint.sh` | Stop, SubagentStop | Yes (exit 2) | Runs `format.sh`, then `ruff check` on the changed and untracked `.py` files. Prints one status line (`format … · lint ✓ (N files)` or `lint -`); on failure, ruff's output goes to stderr and the stop is blocked. |
| `format.sh` | called by `lint.sh` | No | Applies `ruff format` and import sorting to the changed and untracked `.py` files. |

## Why they are built this way

- **Changed files only** (`git diff HEAD` plus untracked, existing `.py` files): fast, and an agent is never blocked by errors it did not touch. The whole-repo check stays `uv run ruff check .` before a commit.
- **Format never blocks.** Formatting never needs the agent's attention; only a lint failure stops the turn.
- **Format runs inside `lint.sh`, in sequence.** Hooks for one event run in parallel; two hooks would race on the same files. `lint.sh` merges the format status into its own line, because a hook's stdout must be one JSON object.
- **Loop breaker.** `stop_hook_active` skips the check once the agent has already continued because of a Stop hook. In addition, `lint.sh` stores an md5 of the failure text in `${TMPDIR:-/tmp}/claude-lint-<session_id>`; the same failure a second time gives a warning and exit 0 instead of another block. A clean run deletes the file.
- **Tree from the payload `.cwd`** (fallback `git rev-parse --show-toplevel`), never `$CLAUDE_PROJECT_DIR`, so a session in a git worktree lints its own tree, not the main checkout.
