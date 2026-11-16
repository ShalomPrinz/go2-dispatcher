"""The prompt surface: the fixed request parts every planner sends (dispatcher/docs/llm.md)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PromptSurface:
    """The fixed request parts, built once per process by ``context.build_prompt_surface``: sent by the planner,
    copied into the run log."""

    system: tuple[str, str]
    catalog_text: str
    tool_schema: dict[str, Any]
    registry_hash: str
    skills: tuple[str, ...]
