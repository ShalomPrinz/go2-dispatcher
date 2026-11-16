"""The report's default skill lists against the registry (analysis/docs/report.md)."""

from __future__ import annotations

from analysis import report
from dispatcher.registry import Registry
from dispatcher.tests.helpers import BUILTIN_SKILLS_DIR


def test_skill_sets_match_the_catalog():
    """The default lists follow each skill's ``moves`` declaration; a new skill must be classified."""
    registry = Registry.load(BUILTIN_SKILLS_DIR)
    for name in registry.names():
        moves = registry[name].moves
        assert (name in report.MOTION_SKILLS) == moves, name
        assert (name in report.STATIONARY_SKILLS) == (not moves), name
