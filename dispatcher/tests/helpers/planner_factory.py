"""``factory()`` for CLI subprocess tests (docs/testing.md): a ScriptedPlanner whose items are the
JSON list in env ``GO2_TEST_SCRIPT`` (each item is raw ``submit_plan`` tool input).

Used as ``GO2_TEST_PLANNER=planner_factory:factory`` with ``dispatcher/tests/helpers`` on PYTHONPATH."""

from __future__ import annotations

import json
import os

try:
    from .planner import ScriptedPlanner
except ImportError:          # imported as a top-level module (dispatcher/tests/helpers on PYTHONPATH)
    from planner import ScriptedPlanner  # type: ignore[no-redef]

SCRIPT_ENV = "GO2_TEST_SCRIPT"


def factory() -> ScriptedPlanner:
    items = json.loads(os.environ.get(SCRIPT_ENV, "[]"))
    if not isinstance(items, list):
        raise ValueError(f"{SCRIPT_ENV} must be a JSON list")
    return ScriptedPlanner(items)
