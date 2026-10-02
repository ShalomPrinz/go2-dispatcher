---
name: worktree
description: Project rules for doing a task in a dedicated git worktree — creating it, restoring the git-ignored files and installs the services need, the branch boundaries, and committing each concern as it lands, and tearing it down. Use only when the user explicitly asks for worktree mode, or asks to create, remove, or clean up a worktree. Never run `git worktree` without reading this skill first.
---

Substitute `<slug>` throughout and take `<repo-root>` from the actual project root.

Pick a short `snake_case` topic slug naming the change (`simplify_registry`, `skill_policy_dataclass`) — not a file name. It names both the branch and the worktree directory.

### Where the work lives — read before anything else

All work happens in a git worktree dedicated to this task. **Create it first, before reading further or touching any file:**

```
bash <repo-root>/.claude/skills/worktree/setup.sh <slug>
```

That one command is the whole setup — there is nothing here for you to decide or check afterwards. It
creates `<repo-root>-<slug>` on a new branch `<slug>` off `main`, copies the git-ignored `.env` and
`config.toml` when main has them, adds the worktree to `permissions.additionalDirectories` in
`<repo-root>/.claude/settings.local.json` so searching it does not prompt, and runs `uv sync` (stub
mode). `runs/` is not copied, so the worktree starts with its own stub posture and logs.

Then run every command from **`<repo-root>-<slug>`**, on the local branch **`<slug>`**. Do not `cd` to
`<repo-root>` — it stays on `main`, untouched.

- Do not merge, rebase onto, fast-forward or otherwise touch `main`. Do not push, do not open a PR.
- Every brief to a project agent (`dispatcher-dev`, `skills-dev`, the reviewer) names
  `<repo-root>-<slug>` as its working directory, so it neither edits nor tests `<repo-root>`.
- The stub stays the backend; the safety rules in CLAUDE.md apply unchanged.

### Commit as you go — not optional

Worktree mode is the one place where committing is pre-authorised: asking for the worktree *is* the
user's authorisation to run `git add` and `git commit` on branch `<slug>`, for this task's changes
only. Nothing else is authorised — no `push`, `stash`, `checkout`, `reset`, merge, or rebase.

A worktree that ends as one large uncommitted diff has failed the task. The whole point of the branch
is a history the user can read commit by commit and revert piecemeal, so commit **during** the work
rather than once at the end:

the moment a concern is finished and coherent on its own, commit it.

Before each commit, in the worktree: `uv run ruff format .`, `uv run ruff check .` and
`uv run pytest -q 2>&1 | tail -n 15` must be clean. One concern per commit, with the docs it changes
in the same commit (CLAUDE.md, Documentation rules). Stage named paths, not `git add -A`. Message:
`<area>: <imperative summary>` as in `git log`, ending with the attribution line the session gives.
You do not need the user to invoke anything; the worktree already carries the authorisation.

Reaching the end of the task with uncommitted work is a process bug, not a handoff: split the
remainder by concern and commit it before you report.

### When the last step is done

Report the branch's commits (`git log --oneline main..<slug>`) and **stop**. Leave the branch sitting
locally for the user to review and merge.

### Teardown

Only when the user asks, and only after the branch's PR is merged:

```
cd <repo-root> && bash <repo-root>/.claude/skills/worktree/teardown.sh <slug>
```

It removes the worktree, both branches, and the `additionalDirectories` entry, and refuses if the
worktree has uncommitted changes or the local tip differs from the merged PR's. `cd` first — the
worktree directory is deleted, so a shell left inside it breaks every later command.

> **Worktree tooling note:** this session's Bash tool refuses heredocs and multi-part commands with redirects while worktree-isolated. Use the Write/Edit tools for file creation, and keep shell commands simple.
