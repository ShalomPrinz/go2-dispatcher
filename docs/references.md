# References

External sources the project depends on or builds on.

## Robot

- **Unitree SDK2 Python** — https://github.com/unitreerobotics/unitree_sdk2_python — The SDK every skill uses to drive the Go2 over DDS (`SportClient`, `SportModeState`). Installed from git through the `robot` extra ([setup.md](setup.md), [robot.md](../skills/docs/robot.md)).

## LLM

- **Anthropic: Sonnet 5.5 migration guide** — https://platform.claude.com/docs/en/models/sonnet-5-5/migration-guide — Source of the parameter restrictions (no non-default `temperature`, no forced `tool_choice`, no `thinking: disabled`) that shape the request ([llm.md](../dispatcher/docs/llm.md)).
- **Anthropic: thinking configuration** — https://platform.claude.com/docs/en/build-with-claude/thinking — How thinking is turned off with `thinking: {"type": "between_tools"}`.
- **Anthropic: structured outputs and strict tool use** — https://platform.claude.com/docs/en/build-with-claude/structured-outputs — Limits of strict mode (no `maxItems`, no numeric ranges), the reason tools are non-strict.

## Transport

- **python-telegram-bot** — https://docs.python-telegram-bot.org — Library behind the Telegram transport; pinned to the 22.x line ([running.md](running.md)).

## Baseline and predecessor

- **OpenClaw documentation** — https://docs.openclaw.ai — The runtime the predecessor used, and the system-level baseline for the study ([project.md](project.md#openclaw-as-a-system-level-baseline)).
- **OpenClaw tool-loop detection** — https://docs.openclaw.ai/tools/loop-detection — Shows that OpenClaw's only loop guard is repetition-based and off by default, part of the case for a purpose-built dispatcher ([architecture.md](architecture.md#why-a-purpose-built-dispatcher)).
- **Predecessor repository** — https://github.com/TamirAshwal/Go2 — Source of the ported skill logic (`walk`, `sit`, `stretch`, `detect_object`).

## Papers

- **OpenGo** — A parameterised skill library on a real Go2. Found medium granularity (fixed functions, LLM-tuned parameters) to be the best trade-off; the motivation for the main study's granularity conditions ([project.md](project.md#main-study)).
- **DoReMi** — Detecting and recovering from misalignment between plan and execution; the conceptual basis for the v2 verification layer ([roadmap.md](roadmap.md#verification-layer)).
