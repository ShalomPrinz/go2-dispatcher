"""Tests for review_preview.py, run as a subprocess in a throwaway git repo (.claude/scripts/README.md)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "review_preview.py"
OWNERSHIP = '{"agents": {"dispatcher-dev": ["dispatcher/"]}, "packages": ["dispatcher/"], "main": []}'
INDEX = "| Document | Owns |\n|---|---|\n| [safety.md](safety.md) | Stops. |\n"


def git(*args: str, cwd: Path) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return done.stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A committed mini project: ownership map, docs index, a safety doc and two dispatcher modules."""
    files = {
        ".claude/ownership.json": OWNERSHIP,
        "docs/README.md": INDEX,
        "docs/safety.md": "The executor kills the group (dispatcher/executor.py).\n",
        "dispatcher/executor.py": "POLL_S = 0.05\n\n\ndef kill():\n    pass\n",
        "dispatcher/loop.py": "from dispatcher.executor import kill\n\nkill()\n",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    git("init", "-q", cwd=tmp_path)
    git("add", "-A", cwd=tmp_path)
    git("commit", "-q", "-m", "init", cwd=tmp_path)
    return tmp_path


def preview(repo: Path) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    done = subprocess.run([sys.executable, str(SCRIPT)], cwd=repo, capture_output=True, text=True, env=env, timeout=10)
    assert done.returncode == 0, done.stderr
    return done.stdout


def test_clean_tree_says_so_and_changes_nothing(repo: Path) -> None:
    assert "No uncommitted changes." in preview(repo)
    assert git("status", "--porcelain", cwd=repo) == ""


def test_untracked_file_content_and_doc_hygiene(repo: Path) -> None:
    (repo / "notes.md").write_text("See [safety](docs/safety.md) and [gone](docs/gone.md).\n")
    (repo / "CLAUDE.md").write_text("Read @docs/safety.md first.\n")
    out = preview(repo)
    assert "Untracked `notes.md` (1 lines):" in out and "See [safety](docs/safety.md)" in out
    assert "`notes.md:1`: broken link `docs/gone.md`" in out and "broken link `docs/safety.md`" not in out
    assert "`CLAUDE.md:1`: `@docs/safety.md` inlines a file" in out
    assert "check 4 not needed" in out
    assert git("status", "--porcelain", cwd=repo).splitlines() == ["?? CLAUDE.md", "?? notes.md"]


def test_stale_reference_changed_value_safety_and_missing_doc(repo: Path) -> None:
    (repo / "dispatcher/executor.py").write_text("POLL_S = 0.1\n\n\ndef stop_group():\n    pass\n")
    out = preview(repo)
    assert "- `kill` removed, still referenced:" in out and "dispatcher/loop.py:1:" in out
    assert "`dispatcher/executor.py:1`: 0.05 -> 0.1" in out
    assert "| `dispatcher/executor.py` | dispatcher-dev | docs/safety.md |" in out
    assert "`dispatcher/executor.py`: package code changed with no owning doc in the diff." in out
    assert "Check 4 needed" in out and "`dispatcher/executor.py`: safety file" in out
