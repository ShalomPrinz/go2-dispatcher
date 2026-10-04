"""PreToolUse hook: deny Edit/Write/NotebookEdit outside the calling agent's scope (.claude/hooks/README.md)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import hooklib

# The shared map loader sits next to the map (.claude/ownership.py), so a worktree uses its own copy.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ownership import covers, load_map, owners  # noqa: E402


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
