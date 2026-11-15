"""Stub control for the dispatcher (services/robot/docs/service.md): the stub reset. Skills never import it, so the
stub world stays off a skill process's import graph."""

from __future__ import annotations

from pathlib import Path

from contract.stub import StubPosture
from services.backend import stub_state


def reset_stub(posture: StubPosture, path: Path) -> None:
    """Put the stub into a known state: ``posture`` at the origin, no command; the dispatcher's only stub write."""
    stub_state.reset(posture, path)
