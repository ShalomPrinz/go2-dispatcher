# Claude Code scripts

Scripts that project agents run themselves; unlike the [hooks](../hooks/README.md), the harness never triggers them. Stdlib and git only, read-only.

## `review_preview.py`

```bash
uv run python .claude/scripts/review_preview.py
```

Prints a Markdown preview of the uncommitted change (staged, unstaged and untracked, against `HEAD`) for the `reviewer` agent, which runs it as its first step ([reviewer.md](../agents/reviewer.md)). It never changes git state and takes well under a second. Every flag is a lead to confirm against the diff, not a finding. Only sections with content are printed:

| Section | What it shows |
|---|---|
| Change set | `git status --short`, `git diff --stat -M HEAD`, and the content of each untracked file (first 60 lines, *tunable*), which `git diff` leaves out. |
| Ownership | Each changed path with its writing agent from [ownership.json](../ownership.json) (or the main session) and its owning docs: the docs indexed in [docs/README.md](../../docs/README.md) that name the path, plus those the file cites in the `(<doc path>)` comment form. A package file outside `tests/` whose owning docs are all absent from the change is flagged. |
| Stale references | Names removed on minus lines and not re-added on any plus line, with up to 5 `git grep` hits each (*tunable*) that still use them: `def`/`class` names and `UPPER_CASE` assignments in `.py` files, quoted snake-case or upper-case codes in `.py` files, and keys in `.toml` files and `config.py`. |
| Changed values | Paired minus/plus lines (in order, within one change) that keep the same shape but differ in numeric or string literals: old values `->` new values, at the new line number. |
| Contract surfaces | Changed files on the fixed list in the script (`CONTRACTS`): fixed texts, `SKILL.md` files, golden files, registry, run log, config, plan and tool schema, skill response, `SKILL.md` schema, skill environment, each with the doc that owns it. |
| Safety trigger | Whether reviewer check 4 applies ([docs/safety.md](../../docs/safety.md)): a change to the executor, `stop_move`, `motion` or `process` (watchdog), or changed package lines (tests excluded) naming the stop path, motion loop, orphan watchdog or secret stripping (`SAFETY_WORDS`). Otherwise it prints "check 4 not needed". |
| Doc hygiene | Broken relative links in changed `.md` files (fenced blocks skipped), `@path` in any `CLAUDE.md`, and `(<doc path>)` citations on changed `.py` lines whose doc does not exist. |

The ownership map, the docs index and the files are read from the reviewed tree, so a worktree is previewed against its own copies. The map loader is the shared `.claude/ownership.py` that `scope.py` also uses.

Tests: `.claude/scripts/tests/test_review_preview.py` runs the script as a subprocess in a throwaway git repo (clean tree, untracked file and doc hygiene, stale reference with changed value, ownership flag and safety trigger); run `uv run pytest .claude/scripts/tests -q`.
