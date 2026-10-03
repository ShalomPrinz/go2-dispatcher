"""PreToolUse hook: deny Edit/Write/NotebookEdit outside the calling agent's scope (.claude/hooks/README.md)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

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


def common_dir(where: Path) -> str | None:
    done = hooklib.git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=str(where))
    return os.fsdecode(done.stdout.strip()) if done.returncode == 0 else None


def repo_relative(path: Path) -> str | None:
    """`path` relative to its own git top level, if that tree is this project's checkout or one of its worktrees."""
    where = path.parent
    while not where.is_dir():
        where = where.parent
    top = hooklib.git("rev-parse", "--show-toplevel", cwd=str(where))
    if top.returncode != 0 or common_dir(where) != common_dir(hooklib.tree()):
        return None
    root = Path(os.fsdecode(top.stdout.strip()))
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return None


def main() -> None:
    data = hooklib.payload()
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        return
    raw = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not isinstance(raw, str) or not raw:
        return
    cwd = data.get("cwd") if isinstance(data.get("cwd"), str) else os.getcwd()
    rel = repo_relative(Path(os.path.realpath(os.path.join(cwd, raw))))
    if rel is None:
        return
    agent = str(data.get("agent_type") or "agent") if data.get("agent_id") else None
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
