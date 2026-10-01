# go2-dispatcher

An LLM dispatcher for the Unitree Go2 EDU: it takes a natural-language task from an operator (CLI or Telegram), asks an LLM for a plan of skill calls through a forced `submit_plan` tool, validates it, runs each step as a separate subprocess against the real robot or a stub, feeds results back to the LLM until it reports `DONE` or `ABORT`, and writes a JSONL run log per task.

## Quick start

```bash
uv sync                                   # core + dev dependencies (stub mode)
cp config.example.toml config.toml        # edit as needed
cp .env.example .env                      # set ANTHROPIC_API_KEY
uv run go2-dispatch catalog
uv run go2-dispatch run "turn left 90 degrees, then tell me if you see a chair"
uv run pytest
```

On the lab machine: `uv sync --extra robot --extra vision`.

## Documentation

- Specification: [docs/spec/](docs/spec/)
- Decisions: [docs/decisions.md](docs/decisions.md)
