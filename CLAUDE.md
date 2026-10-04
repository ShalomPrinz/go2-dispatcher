# CLAUDE.md

Go2 LLM dispatcher: a research system that turns an operator's natural-language task (CLI or Telegram) into a validated plan of skill calls on a Unitree Go2 EDU, runs each skill as a subprocess against the real robot or a stub, and logs every task as JSONL for the study. Python 3.10, managed with `uv`.

**Read [docs/README.md](docs/README.md) first.** It is the index and says which document owns which topic.

## Commands

```bash
uv sync                                        # core + dev deps (stub mode); lab machine: uv sync --extra robot --extra vision
uv run pytest -q                               # default suite: no API key, robot or network needed
uv run ruff format .                           # format; must be clean before a commit
uv run ruff check .                            # lint; must pass before a commit
uv run lint-imports                            # import contracts (skills boundary); must pass before a commit
uv run basedpyright <files>                    # type check; must pass on changed files before a commit
uv run pytest -m integration                   # real subprocesses on the stub backend
uv run go2 catalog                             # system text, catalog, tool schema, registry hash; no key needed
uv run go2 state                               # stub state as JSON
uv run go2 run "turn left 90 degrees"          # one task on the stub (needs ANTHROPIC_API_KEY in .env)
uv run go2 --reset-stub                        # reset the stub posture and exit
```

Package commands (golden files, fault injection, opt-in tests) live in the folder CLAUDE.md files: [dispatcher/CLAUDE.md](dispatcher/CLAUDE.md), [skills/CLAUDE.md](skills/CLAUDE.md), [tests/CLAUDE.md](tests/CLAUDE.md). They load when Claude works in that folder.

## Documentation rules

- The docs (`docs/`, `dispatcher/docs/`, `skills/docs/`, `tests/docs/`) are the source of truth for why the system is built as it is and for its contracts (plan schema, skill response, config keys, outcome codes, log records, fixed texts).
- A change that affects behaviour updates the owning doc **in the same commit**. Never leave docs and code disagreeing.
- Decisions go into the owning doc's "Design decisions" section, with the reason and any rejected alternative. There is no separate decisions log.
- Open questions and pending work go into [docs/roadmap.md](docs/roadmap.md), each with an owner or a way to resolve it. When one is resolved, move the result into the owning doc and remove it from the roadmap.
- Cite docs, not section numbers, in code comments (e.g. `(dispatcher/docs/loop-and-context.md)`). Mark starting values that will be tuned as `(tunable)`.
- No process history and no personal contact details in docs.
- A folder `CLAUDE.md` holds that folder's commands, doc table and gotchas only; rules for the whole repo stay here. It links docs, never repeats them.
- Never put `@` before a doc path in a `CLAUDE.md`: `@path` inlines the file into every session. Use plain links so docs load only when read.

## Safety rules

- **Never run the real backend unattended.** `--backend real` or `robot.backend = "real"` only with a person at the robot and the e-stop in reach ([docs/safety.md](docs/safety.md)).
- The stub is the default backend. Development, tests and demos use the stub.
- Agents never use `--backend real` or set `robot.backend = "real"`, and never run `--run-robot` tests; those are supervised human checks. `--run-live` and `go2 run` call the paid API: only when a brief asks.
- No secrets in code, config files under version control, or logs. The API key and Telegram token live only in `.env` ([docs/configuration.md](docs/configuration.md)).
- Fixed texts in `prompts.py` and `SKILL.md` files change the registry hash and the golden files; changing them makes runs incomparable ([skills/docs/skills.md](skills/docs/skills.md)).

## Engineering rules

- Ask before an architecture decision. "If possible I'd like X" is a question, not approval: answer it, then wait.
- Build only what the current goals need (YAGNI), and build it right: the cleanest design for that scope, never the smallest diff. The cost of moving, renaming or re-testing code is never a reason to keep a weaker design. After implementing, list the complexity left out and the signal that would justify adding it.
- When a workaround fails twice, stop and research the root cause instead of trying a third.
- Match the surrounding code's style, naming and comment density.
- Code comments: one line by default, two at most. The reasoning belongs in the owning doc.

## Working pattern

- The main session is an **orchestrator**: it always delegates code and doc changes to a project agent (`.claude/agents/`), one task at a time, with a self-contained brief written with the `delegate` skill (`.claude/skills/delegate/`). It does not edit `dispatcher/` or `skills/` itself. Exception: the docs under `main` in [.claude/ownership.json](.claude/ownership.json) belong to the main session, which edits them itself; the map assigns every other path.
  - `dispatcher-dev`: anything under `dispatcher/` or owned by `dispatcher/docs/`.
  - `skills-dev`: anything under `skills/` or owned by `skills/docs/`.
  - `tests-dev`: test infrastructure only (pytest plugin, helpers, markers, golden mechanism, coverage, CI) and `tests/docs/testing.md`. Tests of package behaviour go with the package agent.
  - `claude-config-dev`: anything under `.claude/` (agents, skills, hooks, settings). `CLAUDE.md` files go with the agent that owns the folder.
  - A task that touches both packages is split into one task per agent, run in sequence (the side that defines the contract first). Each agent stops and reports when it needs a change on the other side.
- It verifies with commands only (`uv run pytest -q 2>&1 | tail -n 15`, `git status --short`, `git diff --stat`) and commits per task with the `git-commit` skill (`.claude/skills/git-commit/`).
- It does not read the whole doc set or source itself; that exhausts its context before the work starts.
- Subagents record any gap they fill in the owning doc. Dev agents report in 300 words or fewer: files changed, docs updated and why, test and lint results where run, any gap filled in a doc, deviations and decisions made, and anything left open or out of scope (the reviewer has its own format).
- Before each commit, run the `reviewer` subagent (`.claude/agents/reviewer.md`) on the uncommitted diff; after fixing blocking findings, run it again on the fix diff only.
- Keep the test suite fast; prefer fewer, meaningful tests over many parameterised ones.
