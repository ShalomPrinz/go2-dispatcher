"""Ownership map loader and path matching; stdlib only, shared by hooks and scripts (.claude/hooks/README.md)."""

from __future__ import annotations

import json
from pathlib import Path

# The map in this checkout; callers reviewing another tree pass that tree's copy.
MAP = Path(__file__).resolve().parent / "ownership.json"

Scopes = dict[str, tuple[str, ...]]


def load_map(path: Path = MAP) -> tuple[Scopes, tuple[str, ...]] | None:
    """Per-agent write prefixes and the package prefixes, or None when the map is missing or malformed."""
    try:
        data = json.loads(path.read_text())
        agents, packages = data["agents"], data["packages"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    lists = [*agents.values(), packages] if isinstance(agents, dict) else [None]
    if not all(isinstance(ps, list) and all(isinstance(p, str) for p in ps) for ps in lists):
        return None
    return {a: tuple(ps) for a, ps in agents.items()}, tuple(packages)


def covers(prefixes: tuple[str, ...], rel: str) -> bool:
    """`rel` matches a prefix: a trailing `/` is a folder, `**/name` that file name at any depth, else one file."""
    return any(
        rel == p or (p.endswith("/") and rel.startswith(p)) or (p.startswith("**/") and f"/{rel}".endswith(p[2:]))
        for p in prefixes
    )


def owners(scopes: Scopes, rel: str) -> str:
    """The agents whose scope covers `rel`, or the main session if none does."""
    return ", ".join(a for a, prefixes in scopes.items() if covers(prefixes, rel)) or "the main session"
