---
name: git-commit
description: Project rules for committing work in this repo: what to check first, how to split changes into commits, and the one-line message format. Use whenever you commit, including the orchestrator's commit after each delegated task.
---

Invoking this skill authorises `git add` and `git commit` for the changes at hand, nothing else. Never `push`, `stash`, `checkout`, `reset`, merge or rebase. Project subagents never commit; the main session does.

Start by reading the actual diff (`git status --short`, `git diff`, `git diff --staged`). Commit only what the task produced: unrelated pre-existing changes and untracked notes (for example `backlog.md`) stay in the working tree. If you cannot tell whether a change belongs, ask.

### Before committing

- `uv run ruff format --check .`, `uv run ruff check .` and `uv run lint-imports` pass.
- `uv run pytest -q 2>&1 | tail -n 15` passes. If golden files changed, the diff was reviewed and is caused by the task (fixed-text changes make runs incomparable, see CLAUDE.md).
- A behaviour change carries its owning-doc update in the same commit (CLAUDE.md, Documentation rules).
- Nothing secret is staged: never `.env`, API keys or tokens.

### One commit per concern

A task with several independent items is several commits, one per item, so each is reviewable and revertable on its own.

- Split by **concern**, not by file. Two concerns in one file are two commits; one concern across six files is one commit.
- A refactor or rename a concern needs belongs in that concern's commit, unless it stands alone, in which case it goes first.
- Stage each concern by path (`git add <paths>`, or `git add -u` when every tracked change belongs), never `git add -A`.

### Message format

One line, imperative, `<area>: <what the change does>`. The area is the module or concern, as in the existing history:

```
registry: parse SKILL.md frontmatter with pydantic models
docs: move testing.md to tests/docs/ under tests-dev ownership
tooling: deny real backend, robot tests and .env reads to Claude Code
ci: support and test Python 3.10 only (cyclonedds wheel)
```

No body: no bullet list, no "why" paragraph. The why belongs in the owning doc's "Design decisions". If one line will not fit, the commit is two concerns; split it. The only allowed extra lines are a blank line and the `Co-Authored-By: Claude ...` trailer. Never add a `Claude-Session:` trailer.

Pass the message with `-m` (one `-m` for the line, one for the trailer); no heredocs.

### After committing

Report the resulting commits (`git log --oneline -n <count>`) and stop. Leave them local for the user to review and push.
