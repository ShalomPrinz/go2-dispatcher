"""Stop/SubagentStop hook: block while `basedpyright` fails on the .py files the agent wrote, or `lint-imports` fails.

Never writes files, so it runs in parallel with lint.py safely (.claude/hooks/README.md).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import hooklib

# basedpyright `include` in pyproject.toml; explicit file arguments bypass it, so filter here.
TYPED_ROOTS = ("dispatcher/", "skills/", "tests/")


def start(root: Path, *cmd: str) -> subprocess.Popen[str]:
    return subprocess.Popen(cmd, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def main() -> None:
    if hooklib.stop_hook_active():
        return
    trees = hooklib.touched("typecheck")
    if not trees:
        hooklib.passed("typecheck")
        hooklib.report("types -")
        return

    checks: list[tuple[str, str, subprocess.Popen[str]]] = []
    typed_count = 0
    for root, files in trees.items():
        where = f"cd {root} && " if len(trees) > 1 else ""
        typed = [f for f in files if f.startswith(TYPED_ROOTS)]
        typed_count += len(typed)
        if typed:
            types = start(root, "uv", "run", "basedpyright", *typed)
            checks.append(("types", f"{where}uv run basedpyright {' '.join(typed)}", types))
        checks.append(("imports", f"{where}uv run lint-imports", start(root, "uv", "run", "lint-imports", "--no-logo")))

    failed: list[tuple[str, str, str]] = []
    for label, command, proc in checks:
        out, _ = proc.communicate()
        if proc.returncode != 0:
            failed.append((label, command, out))
    bad = {label for label, _, _ in failed}
    status = ["types -"] if not typed_count else [] if "types" in bad else [f"types ✓ ({typed_count} files)"]
    status += [] if "imports" in bad else ["imports ✓"]
    if not failed:
        hooklib.passed("typecheck")
        hooklib.report(" · ".join(status))
        return
    hooklib.block(
        "typecheck",
        "\n".join(f"$ {command}\n{out.rstrip()}" for _, command, out in failed),
        reason="Type check or import contracts failed; fix them before stopping:",
        command="; ".join(command for _, command, _ in failed),
        done=status,
    )


if __name__ == "__main__":
    main()
