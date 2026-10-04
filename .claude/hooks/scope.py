"""PreToolUse hook: deny Edit/Write/NotebookEdit outside the calling agent's scope (.claude/hooks/README.md)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import hooklib

# The ownership map (.claude/hooks/README.md); read from the hook's own checkout, so a worktree uses its copy.
OWNERSHIP = Path(__file__).resolve().parent.parent / "ownership.json"


def load_map() -> tuple[dict[str, tuple[str, ...]], tuple[str, ...]] | None:
    """Per-agent write prefixes and the package prefixes, or None when the map is missing or malformed."""
    try:
        data = json.loads(OWNERSHIP.read_text())
        agents, packages = data["agents"], data["packages"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    lists = [*agents.values(), packages] if isinstance(agents, dict) else [None]
    if not all(isinstance(ps, list) and all(isinstance(p, str) for p in ps) for ps in lists):
        return None
    return {a: tuple(ps) for a, ps in agents.items()}, tuple(packages)


def covers(prefixes: tuple[str, ...], rel: str) -> bool:
    return any(
        rel == p or (p.endswith("/") and rel.startswith(p)) or (p.startswith("**/") and f"/{rel}".endswith(p[2:]))
        for p in prefixes
    )


def owners(scopes: dict[str, tuple[str, ...]], rel: str) -> str:
    """The agents whose scope covers `rel`, or the main session if none does."""
    return ", ".join(a for a, prefixes in scopes.items() if covers(prefixes, rel)) or "the main session"


def main() -> None:
    path = hooklib.written_path()
    found = hooklib.locate(path) if path else None
    ownership = load_map()
    if found is None or ownership is None:
        return
    scopes, packages = ownership
    rel = found[1]
    agent = hooklib.agent_type()
    if agent in scopes:
        if covers(scopes[agent], rel):
            return
        why = f"outside the {agent} scope (.claude/agents/{agent}.md)"
    elif covers(packages, rel):
        why = "in a package, which only its project agent edits (CLAUDE.md, Working pattern)"
    else:
        return
    who = agent or "The main session"
    reason = (
        f"{who} may not write {rel}: {why}. In scope of: {owners(scopes, rel)}; delegate or report the change instead."
    )
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )


if __name__ == "__main__":
    main()
    sys.exit(0)
