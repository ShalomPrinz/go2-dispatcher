"""PreToolUse hook: deny Edit/Write/NotebookEdit outside the calling agent's scope (.claude/hooks/README.md)."""

from __future__ import annotations

import json
import sys

import hooklib

# Repo-relative prefixes each project agent may write: `x/` a folder, `**/name` that file name anywhere, else one file.
SCOPES: dict[str, tuple[str, ...]] = {
    "dispatcher-dev": (
        "dispatcher/",
        "tests/integration/",
        "docs/configuration.md",
        "docs/safety.md",
        "docs/running.md",
        "docs/roadmap.md",
    ),
    "skills-dev": (
        "skills/",
        "tests/integration/",
        "docs/safety.md",
        "docs/configuration.md",
        "docs/setup.md",
        "docs/roadmap.md",
    ),
    "tests-dev": (
        "tests/",
        "**/conftest.py",
        "dispatcher/tests/",
        "skills/tests/",
        "pyproject.toml",
        ".github/workflows/tests.yml",
    ),
    "claude-config-dev": (".claude/agents/", ".claude/skills/", ".claude/hooks/", ".claude/settings.json"),
    "reviewer": (),
}
# Denied to the main session and to any agent without an entry above (built-ins such as general-purpose).
PACKAGES = ("dispatcher/", "skills/")


def covers(prefixes: tuple[str, ...], rel: str) -> bool:
    return any(
        rel == p or (p.endswith("/") and rel.startswith(p)) or (p.startswith("**/") and f"/{rel}".endswith(p[2:]))
        for p in prefixes
    )


def owners(rel: str) -> str:
    """The agents whose scope covers `rel`, or the main session if none does."""
    return ", ".join(a for a, prefixes in SCOPES.items() if covers(prefixes, rel)) or "the main session"


def main() -> None:
    path = hooklib.written_path()
    found = hooklib.locate(path) if path else None
    if found is None:
        return
    rel = found[1]
    agent = hooklib.agent_type()
    if agent in SCOPES:
        if covers(SCOPES[agent], rel):
            return
        why = f"outside the {agent} scope (.claude/agents/{agent}.md)"
    elif covers(PACKAGES, rel):
        why = "in a package, which only its project agent edits (CLAUDE.md, Working pattern)"
    else:
        return
    who = agent or "The main session"
    reason = f"{who} may not write {rel}: {why}. In scope of: {owners(rel)}; delegate or report the change instead."
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
