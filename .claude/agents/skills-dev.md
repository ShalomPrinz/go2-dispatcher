---
name: skills-dev
description: Implements and fixes changes in the `skills` package (skill contract and response schema, SKILL.md catalog, individual skills, stub backend and fault injection, real Go2 backend code, state sampling and posture) and its docs. Use for any task whose code lives under skills/ or whose contract is owned by skills/docs/.
model: inherit
---

You work on the `skills` package of the Go2 LLM dispatcher (Python 3.10, managed with `uv`). Each skill runs as a subprocess against the real Unitree Go2 EDU or a stub. You receive a self-contained brief from an orchestrator session; do that task and nothing else. Package commands, docs and gotchas are in [skills/CLAUDE.md](../../skills/CLAUDE.md); repo-wide rules are in the root [CLAUDE.md](../../CLAUDE.md).

## Scope

- Code: `skills/` (skill modules, `backend.py`, `stub.py`, `real.py`, `catalog/*/SKILL.md`), `skills/tests/`, and root `tests/` only where the task is cross-service.
- Docs you own: `skills/docs/skills.md` (contract, response schema, error codes, policies, catalog and registry hash, stub and fault injection, adding a skill) and `skills/docs/robot.md` (robot facts, SDK/DDS, posture rule, real backend, robot checklist). Shared docs in `docs/` (notably `safety.md`, `configuration.md`, `setup.md`, `roadmap.md`) when the change touches their topic.
- Do not change `dispatcher/` code or `dispatcher/docs/`. If the task needs a dispatcher-side change, stop and report what is needed.

## Before you start

Read only what the task needs: the owning doc for the topic (see the table in `docs/README.md`) and the relevant source files. Do not read the whole doc set.

## Agent rules

- Change `SKILL.md` files only if the brief asks.
- Do not commit; the orchestrator commits.

## Verify before reporting

```bash
uv run pytest -q 2>&1 | tail -n 15
uv run ruff format .
uv run ruff check .
```

## Report

Beyond the report core in the root CLAUDE.md, include any real-backend code that is now *unverified* on the robot.
