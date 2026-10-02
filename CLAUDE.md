# CLAUDE.md

Go2 LLM dispatcher: a research system that turns an operator's natural-language task (CLI or Telegram) into a validated plan of skill calls on a Unitree Go2 EDU, runs each skill as a subprocess against the real robot or a stub, and logs every task as JSONL for the study. Python 3.10, managed with `uv`.

**Read [docs/README.md](docs/README.md) first.** It is the index and says which document owns which topic.

## Commands

```bash
uv sync                                        # core + dev deps (stub mode); lab machine: uv sync --extra robot --extra vision
uv run pytest -q                               # default suite: no API key, robot or network needed
uv run ruff format .                           # format; must be clean before a commit
uv run ruff check .                            # lint; must pass before a commit
uv run pytest -m integration                   # real subprocesses on the stub backend
uv run pytest --update-golden                  # regenerate golden files; review the diff
uv run go2 catalog                             # system text, catalog, tool schema, registry hash; no key needed
uv run go2 state                               # stub state as JSON
uv run go2 run "turn left 90 degrees"          # one task on the stub (needs ANTHROPIC_API_KEY in .env)
uv run go2 --fault 1:hang run "…"              # stub fault injection (skills/docs/skills.md)
uv run go2 --reset-stub                        # reset the stub posture and exit
```

Opt-in tests (`--run-live`, `--run-robot`) are described in [docs/testing.md](docs/testing.md).

## Documentation rules

- The docs (`docs/`, `dispatcher/docs/`, `skills/docs/`) are the source of truth for why the system is built as it is and for its contracts (plan schema, skill response, config keys, outcome codes, log records, fixed texts).
- A change that affects behaviour updates the owning doc **in the same commit**. Never leave docs and code disagreeing.
- Decisions go into the owning doc's "Design decisions" section, with the reason and any rejected alternative. There is no separate decisions log.
- Open questions and pending work go into [docs/roadmap.md](docs/roadmap.md), each with an owner or a way to resolve it. When one is resolved, move the result into the owning doc and remove it from the roadmap.
- Cite docs, not section numbers, in code comments (e.g. `(dispatcher/docs/loop-and-context.md)`). Mark starting values that will be tuned as `(tunable)`.
- No process history and no personal contact details in docs.

## Safety rules

- **Never run the real backend unattended.** `--backend real` or `robot.backend = "real"` only with a person at the robot and the e-stop in reach ([docs/safety.md](docs/safety.md)).
- The stub is the default backend. Development, tests and demos use the stub.
- Opt-in robot tests (`--run-robot`) need a supervised session; never run them from an agent.
- No secrets in code, config files under version control, or logs. The API key and Telegram token live only in `.env` ([docs/configuration.md](docs/configuration.md)).
- Fixed texts in `prompts.py` and `SKILL.md` files change the registry hash and the golden files; changing them makes runs incomparable ([skills/docs/skills.md](skills/docs/skills.md)).

## Working pattern

- The main session is an **orchestrator**: it always delegates code and doc changes to a project agent (`.claude/agents/`), one task at a time, with a self-contained brief. It does not edit `dispatcher/` or `skills/` itself.
  - `dispatcher-dev`: anything under `dispatcher/` or owned by `dispatcher/docs/`.
  - `skills-dev`: anything under `skills/` or owned by `skills/docs/`.
  - A task that touches both packages is split into one task per agent, run in sequence (the side that defines the contract first). Each agent stops and reports when it needs a change on the other side.
- It verifies with commands only (`uv run pytest -q 2>&1 | tail -n 15`, `git status --short`, `git diff --stat`) and commits per task.
- It does not read the whole doc set or source itself; that exhausts its context before the work starts.
- Subagents record any gap they fill in the owning doc and report briefly (300 words or fewer).
- End with a fresh, read-only reviewer subagent that checks conformance with the docs.
- Keep the test suite fast; prefer fewer, meaningful tests over many parameterised ones.
