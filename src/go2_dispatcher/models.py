"""Data models and exceptions (§6)."""

from __future__ import annotations

from typing import Literal


# --- Exceptions (§6.6) -------------------------------------------------------


class ConfigError(Exception):
    """Invalid configuration (unknown key, invalid value, missing explicit file)."""


class RegistryError(Exception):
    """Invalid skill set; the message names the offending file."""


class BusyError(Exception):
    """run_task called while a task is running."""


class LLMUnavailable(Exception):
    """Infra retries exhausted, or a non-retryable API error."""

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class LLMInterrupted(Exception):
    """Stop or task deadline hit while waiting to retry an LLM call."""

    def __init__(self, cause: Literal["operator", "task_time_limit"]):
        super().__init__(cause)
        self.cause = cause
