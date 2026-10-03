"""Stop/SubagentStop hook: format the .py files the stopping agent wrote, then block while `ruff check` fails on them.

Format runs here, before the lint, because hooks on one event run in parallel (.claude/hooks/README.md).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import hooklib


def digests(root: Path, files: list[str]) -> list[str]:
    return [hashlib.md5((root / f).read_bytes()).hexdigest() for f in files]


def main() -> None:
    if hooklib.stop_hook_active():
        return
    trees = hooklib.touched("lint")
    if not trees:
        hooklib.passed("lint")
        hooklib.report("format - · lint -")
        return

    formatted, failures = 0, []
    for root, files in trees.items():
        before = digests(root, files)
        hooklib.run("uv", "run", "ruff", "format", "--quiet", "--", *files, cwd=root)
        hooklib.run("uv", "run", "ruff", "check", "--select", "I", "--fix", "--quiet", "--", *files, cwd=root)
        formatted += sum(a != b for a, b in zip(before, digests(root, files), strict=True))
        lint = hooklib.run("uv", "run", "ruff", "check", "--force-exclude", "--", *files, cwd=root)
        if lint.returncode != 0:
            failures.append(f"(in {root})\n{lint.stdout}" if len(trees) > 1 else lint.stdout)
    fmt = f"format ✓ ({formatted} files)" if formatted else "format -"

    if not failures:
        hooklib.passed("lint")
        hooklib.report(f"{fmt} · lint ✓ ({sum(map(len, trees.values()))} files)")
        return
    hooklib.block(
        "lint",
        "\n".join(failures),
        reason="ruff check failed; fix the lint errors before stopping:",
        command="uv run ruff check .",
        done=[fmt],
    )


if __name__ == "__main__":
    main()
