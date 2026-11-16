"""The ``task_start`` fields that are constant for the process (dispatcher/docs/run-log.md)."""

from __future__ import annotations

import importlib.metadata
import platform
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from ..config import Config

if TYPE_CHECKING:
    from ..llm.surface import PromptSurface

GIT_TIMEOUT_S = 5.0  # best-effort `git rev-parse HEAD` at startup
VERSION_PACKAGES = ("anthropic", "httpx", "pydantic", "go2-dispatcher")


def _versions() -> dict[str, str | None]:
    out: dict[str, str | None] = {"python": platform.python_version()}
    for pkg in VERSION_PACKAGES:
        try:
            out[pkg] = importlib.metadata.version(pkg)
        except importlib.metadata.PackageNotFoundError:
            out[pkg] = None
    return out


def _git_commit(base_dir: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=base_dir, capture_output=True, text=True, timeout=GIT_TIMEOUT_S
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


class SessionInfo(BaseModel):
    """The ``task_start`` fields that are constant for the process, collected once at startup."""

    condition: str
    config: Config
    registry_hash: str
    system_text: str
    catalog_text: str
    tool_schema: dict[str, Any]
    skills: list[str]
    versions: dict[str, str | None]
    git_commit: str | None

    @classmethod
    def collect(cls, cfg: Config, surface: PromptSurface) -> SessionInfo:
        return cls(
            condition=cfg.run.condition,
            config=cfg,
            registry_hash=surface.registry_hash,
            system_text=surface.system[0],
            catalog_text=surface.catalog_text,
            tool_schema=surface.tool_schema,
            skills=list(surface.skills),
            versions=_versions(),
            git_commit=_git_commit(cfg.base_dir),
        )
