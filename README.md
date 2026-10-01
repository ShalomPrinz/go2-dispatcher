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

All project documentation is in [docs/](docs/README.md): start with the index there.
