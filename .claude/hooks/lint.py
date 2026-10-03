"""Stop/SubagentStop hook: format the changed .py files, then block while `ruff check` fails on them.

Format runs here, before the lint, because hooks on one event run in parallel (.claude/hooks/README.md).
"""

from __future__ import annotations

import hashlib

import hooklib


def digests(files: list[str]) -> list[str]:
    return [hashlib.md5((hooklib.tree() / f).read_bytes()).hexdigest() for f in files]


def main() -> None:
    if hooklib.stop_hook_active():
        return
    files = hooklib.changed_files()
    if not files:
        hooklib.report("format - · lint -")
        return

    before = digests(files)
    hooklib.run("uv", "run", "ruff", "format", "--quiet", "--", *files)
    hooklib.run("uv", "run", "ruff", "check", "--select", "I", "--fix", "--quiet", "--", *files)
    formatted = sum(a != b for a, b in zip(before, digests(files), strict=True))
    fmt = f"format ✓ ({formatted} files)" if formatted else "format -"

    lint = hooklib.run("uv", "run", "ruff", "check", "--force-exclude", "--", *files)
    if lint.returncode == 0:
        hooklib.passed("lint")
        hooklib.report(f"{fmt} · lint ✓ ({len(files)} files)")
        return
    hooklib.block(
        "lint",
        lint.stdout,
        reason="ruff check failed; fix the lint errors before stopping:",
        command="uv run ruff check .",
        done=[fmt],
    )


if __name__ == "__main__":
    main()
