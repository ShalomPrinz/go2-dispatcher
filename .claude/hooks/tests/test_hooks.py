"""Tests for the Claude Code hooks, run as subprocesses with a JSON payload on stdin (.claude/hooks/README.md)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parent.parent
SESSION = "s1"


def git(*args: str, cwd: Path) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True, capture_output=True
    )


@pytest.fixture(scope="module")
def repo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A throwaway git repo with one commit, so the hooks never see the real checkout."""
    root = (tmp_path_factory.mktemp("repo") / "proj").resolve()
    root.mkdir()
    git("init", "-q", cwd=root)
    git("commit", "-q", "--allow-empty", "-m", "init", cwd=root)
    return root


@pytest.fixture
def tmpdir_env(tmp_path: Path) -> Path:
    """`TMPDIR` for the hook, so the touched-files records land in a pytest tmp dir."""
    (tmp_path / "state").mkdir()
    return tmp_path / "state"


def hook(name: str, payload: object, tmpdir: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | {"TMPDIR": str(tmpdir)}
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(HOOKS / name)], input=stdin, capture_output=True, text=True, env=env, timeout=10
    )


def write(path: str, cwd: Path, agent: str | None = None) -> dict[str, object]:
    data: dict[str, object] = {"session_id": SESSION, "cwd": str(cwd), "tool_name": "Write"}
    data["tool_input"] = {"file_path": path, "content": "x"}
    if agent:
        data |= {"agent_id": f"id-{agent}", "agent_type": agent}
    return data


def decision(name: str, payload: object, tmpdir: Path) -> str:
    """`allow` when the hook exits 0 silently, else the reason of its deny JSON."""
    done = hook(name, payload, tmpdir)
    assert done.returncode == 0, done.stderr
    if not done.stdout.strip():
        return "allow"
    out = json.loads(done.stdout)["hookSpecificOutput"]
    assert (out["hookEventName"], out["permissionDecision"]) == ("PreToolUse", "deny")
    return out["permissionDecisionReason"]


def test_scope_main_session_and_builtin_agents_denied_packages_only(repo: Path, tmpdir_env: Path) -> None:
    for agent in (None, "general-purpose"):
        for rel in ("dispatcher/loop.py", "skills/catalog/x/SKILL.md"):
            reason = decision("scope.py", write(rel, repo, agent), tmpdir_env)
            assert rel in reason and "in a package" in reason
        for rel in ("docs/roadmap.md", "tests/test_x.py", ".claude/settings.json", "README.md"):
            assert decision("scope.py", write(rel, repo, agent), tmpdir_env) == "allow"


def test_scope_project_agent_allowed_in_scope_and_denied_outside(repo: Path, tmpdir_env: Path) -> None:
    for rel in ("dispatcher/loop.py", "tests/integration/test_x.py", str(repo / "docs/safety.md")):
        assert decision("scope.py", write(rel, repo, "dispatcher-dev"), tmpdir_env) == "allow"
    reason = decision("scope.py", write("skills/x.py", repo, "dispatcher-dev"), tmpdir_env)
    assert "outside the dispatcher-dev scope" in reason and "In scope of: skills-dev" in reason
    for rel in ("tests/test_x.py", "docs/README.md", ".claude/settings.json"):
        assert decision("scope.py", write(rel, repo, "dispatcher-dev"), tmpdir_env) != "allow"


def test_scope_reviewer_denied_everything(repo: Path, tmpdir_env: Path) -> None:
    for rel in ("README.md", "docs/roadmap.md", "tests/test_x.py", ".claude/hooks/x.py"):
        assert "outside the reviewer scope" in decision("scope.py", write(rel, repo, "reviewer"), tmpdir_env)


def test_scope_conftest_anywhere_for_tests_dev(repo: Path, tmpdir_env: Path) -> None:
    for rel in ("conftest.py", "dispatcher/tests/conftest.py", "skills/catalog/x/conftest.py"):
        assert decision("scope.py", write(rel, repo, "tests-dev"), tmpdir_env) == "allow"
    assert decision("scope.py", write("skills/catalog/x/notconftest.py", repo, "tests-dev"), tmpdir_env) != "allow"


def test_scope_worktree_checked_like_the_checkout(repo: Path, tmpdir_env: Path, tmp_path: Path) -> None:
    worktree = tmp_path / "proj-wt"
    git("worktree", "add", "-q", "--detach", str(worktree), cwd=repo)
    try:
        assert decision("scope.py", write(str(worktree / "skills/x.py"), repo, "skills-dev"), tmpdir_env) == "allow"
        assert "in a package" in decision("scope.py", write(str(worktree / "skills/x.py"), repo), tmpdir_env)
    finally:
        git("worktree", "remove", "--force", str(worktree), cwd=repo)


def test_scope_allows_outside_the_project_and_bad_payloads(repo: Path, tmpdir_env: Path, tmp_path: Path) -> None:
    outside = tmp_path / "loose"  # pytest tmp dirs are outside any git tree
    outside.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    git("init", "-q", cwd=scratch)
    for path in (str(outside / "dispatcher/x.py"), str(scratch / "dispatcher/x.py")):
        assert decision("scope.py", write(path, repo, "reviewer"), tmpdir_env) == "allow"
    for bad in ("", "not json", "[]", {"cwd": str(repo), "tool_input": "dispatcher/x.py"}, {"cwd": str(repo)}):
        assert decision("scope.py", bad, tmpdir_env) == "allow"


def test_touched_records_per_caller_and_only_project_paths(repo: Path, tmpdir_env: Path, tmp_path: Path) -> None:
    for payload in (write("a.py", repo), write("b.py", repo, "skills-dev"), write(str(tmp_path / "c.py"), repo)):
        done = hook("touched.py", payload, tmpdir_env)
        assert (done.returncode, done.stdout) == (0, "")
    records = {p.name: p.read_text().splitlines() for p in tmpdir_env.iterdir()}
    assert records == {
        f"claude-touched-{SESSION}-main": [str(repo / "a.py")],
        f"claude-touched-{SESSION}-id-skills-dev": [str(repo / "b.py")],
    }


@pytest.mark.parametrize("name", ["lint.py", "typecheck.py"])
def test_stop_hooks_exit_at_once_when_stop_hook_active(name: str, repo: Path, tmpdir_env: Path) -> None:
    done = hook(name, {"session_id": SESSION, "cwd": str(repo), "stop_hook_active": True}, tmpdir_env)
    assert (done.returncode, done.stdout, done.stderr) == (0, "", "")
