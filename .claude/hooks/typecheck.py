"""Stop/SubagentStop hook: block while `basedpyright` fails on the changed .py files or `lint-imports` fails.

Never writes files, so it runs in parallel with lint.py safely (.claude/hooks/README.md).
"""

from __future__ import annotations

import subprocess

import hooklib

# basedpyright `include` in pyproject.toml; explicit file arguments bypass it, so filter here.
TYPED_ROOTS = ("dispatcher/", "skills/", "tests/")


def start(*cmd: str) -> subprocess.Popen[str]:
    return subprocess.Popen(cmd, cwd=hooklib.tree(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def main() -> None:
    if hooklib.stop_hook_active():
        return
    files = hooklib.changed_files()
    if not files:
        hooklib.report("types -")
        return

    typed = [f for f in files if f.startswith(TYPED_ROOTS)]
    imports = start("uv", "run", "lint-imports", "--no-logo")
    types = start("uv", "run", "basedpyright", *typed) if typed else None
    checks = [("imports", "uv run lint-imports", imports)]
    if types:
        checks.insert(0, ("types", f"uv run basedpyright {' '.join(typed)}", types))

    status = [] if types else ["types -"]
    failed: list[tuple[str, str]] = []
    for label, command, proc in checks:
        out, _ = proc.communicate()
        if proc.returncode == 0:
            status.append(f"{label} ✓ ({len(typed)} files)" if label == "types" else f"{label} ✓")
        else:
            failed.append((command, out))
    if not failed:
        hooklib.passed("typecheck")
        hooklib.report(" · ".join(status))
        return
    hooklib.block(
        "typecheck",
        "\n".join(f"$ {command}\n{out.rstrip()}" for command, out in failed),
        reason="Type check or import contracts failed; fix them before stopping:",
        command="; ".join(command for command, _ in failed),
        done=status,
    )


if __name__ == "__main__":
    main()
