"""PostToolUse hook: record each file an edit tool wrote, per caller, for the Stop checks (.claude/hooks/README.md)."""

from __future__ import annotations

import hooklib


def main() -> None:
    path = hooklib.written_path()
    if path and hooklib.locate(path):
        hooklib.record(path)


if __name__ == "__main__":
    main()
