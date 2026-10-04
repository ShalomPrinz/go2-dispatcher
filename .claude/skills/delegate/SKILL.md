---
name: delegate
description: Turn a task into a self-contained brief and hand it to the owning project agent (dispatcher-dev, skills-dev, tests-dev, claude-config-dev). Use whenever the main session delegates code or doc work, which CLAUDE.md requires for every change.
---

The main session orchestrates; project agents do the work (CLAUDE.md, Working pattern). A subagent starts cold: it knows only CLAUDE.md, its own agent file and the brief. Everything else it needs must be in the brief.

### 1. Pick the owner

The agent whose list in `.claude/ownership.json` covers the files the task changes owns it (`x/` a folder, `**/name` that file name anywhere, else one file); the ownership table in `docs/README.md` says which doc owns which topic. Test infrastructure goes to `tests-dev`, but tests of package behaviour go with the package agent. Paths under `main` in the map are the main session's own: edit those yourself, do not delegate them.

A task that touches two owners becomes one brief per agent, run in sequence, with the side that defines the contract first. Pass the first agent's report into the second brief.

### 2. Scope it to what the user asked

Brief exactly the items the user named. If the request is ambiguous (for example "do all of P1" when P1 has sections), ask before delegating. Delegated scope creep is expensive to undo.

### 3. Write the brief

Keep it short and concrete, with paths rather than explanations of the code:

```
Goal: <one sentence: the outcome, not the steps>
Context: <what exists now, with paths; decisions the user already made; why>
Owning doc: <doc to update in the same change, and whether a Design decisions bullet is expected>
Do: <numbered items, each checkable>
Out of scope: <what not to touch; other owners' code; untracked files in the root>
Verify: <commands to run; the expected result, e.g. test count unchanged, golden files unchanged>
Report: <only what this task needs beyond the report core in CLAUDE.md, e.g. a lower word limit or a verbatim snippet; omit if nothing>
```

Name the user's explicit decisions in Context so the agent does not re-open them. Name any risky step and what to do instead, for example "if golden files fail, stop and report; do not run --update-golden".

### 4. Launch and wait

Launch with the Agent tool and the agent's `subagent_type`, one task at a time. Do not read the source or the doc set yourself while it runs. If the user narrows the scope mid-run, message the agent at once with what to drop and what to revert.

### 5. After the report

- Verify with commands only: `uv run pytest -q 2>&1 | tail -n 15`, `git status --short`, `git diff --stat`.
- Check the report for deviations and decisions, and relay them to the user.
- Commit with the `git-commit` skill.
- When the work is done, run a fresh read-only reviewer subagent that checks conformance with the docs.
