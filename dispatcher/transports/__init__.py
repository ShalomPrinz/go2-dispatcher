"""Transports (CLI, Telegram): parse input and present output over a dispatcher that ``dispatcher.app`` builds
(docs/running.md)."""

from __future__ import annotations

from ..models import ConfigError, RegistryError, StartupError

__all__ = ["startup_error_text"]


def startup_error_text(e: StartupError) -> str:
    """Stderr line for a start-up error; the entry point prints it and exits 2 (docs/running.md)."""
    if isinstance(e, ConfigError):
        return f"Config error: {e}"
    if isinstance(e, RegistryError):
        return f"Registry error: {e}"
    return str(e)
