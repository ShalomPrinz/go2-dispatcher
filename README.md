# go2-dispatcher

An LLM dispatcher for the Unitree Go2 EDU: it takes a natural-language task from an operator (CLI or Telegram), asks an LLM for a plan of skill calls through a single `submit_plan` tool, validates it, runs each step as a separate subprocess against the real robot or a stub, feeds results back to the LLM until it reports `DONE` or `ABORT`, and writes a JSONL run log per task.

## Quick start

```bash
uv sync                                   # core + dev dependencies (stub mode)
cp config.example.toml config.toml && cp .env.example .env   # then set ANTHROPIC_API_KEY in .env
uv run go2-dispatch catalog               # no API key needed
uv run go2-dispatch run "turn left 90 degrees, then tell me if you see a chair"
uv run pytest
```

On the lab machine: `uv sync --extra robot --extra vision` (see [setup](docs/setup.md)).

## Documentation

- [Architecture](docs/architecture.md): overview of the design
- [Setup](docs/setup.md): install, lab machine, verification
- [Configuration](docs/configuration.md): every config key, `.env`, overrides
- [Running](docs/running.md): CLI, Telegram, stub vs real, fault injection
- [Skills](docs/skills.md): SKILL.md format, response schema, adding a skill
- [Loop and context](docs/loop-and-context.md): the loop, budgets, what the model sees
- [Safety](docs/safety.md): stop path, limits, supervised operation
- [Run log](docs/run-log.md): record types and study metrics
- [Testing](docs/testing.md): test suite and the robot checklist
- [Decisions](docs/decisions.md), [Open decisions](docs/open-decisions.md), [Future ideas](docs/future-ideas.md)
- Specification: [docs/spec/](docs/spec/)
