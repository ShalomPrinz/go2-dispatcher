"""Run go2_skills modules as subprocesses on the stub backend (docs/testing.md)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import REPO_ROOT, TEST_TIME_SCALE

SECRET_ENV = ("ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN")
SKILL_RUN_TIMEOUT_S = 20.0


def stub_env(tmp_path: Path, *, detections: dict[str, str] | None = None,
             fault: str | None = None, **extra: str) -> dict[str, str]:
    """Environment like the executor's (docs/skills.md) for the stub backend, rooted at tmp_path."""
    env = {k: v for k, v in os.environ.items()
           if k not in SECRET_ENV and not k.startswith("GO2_")}
    env.update({
        "PYTHONUNBUFFERED": "1",
        "GO2_BACKEND": "stub",
        "GO2_IFACE": "",
        "GO2_YOLO_WEIGHTS": str(tmp_path / "missing.pt"),
        "GO2_STUB_STATE_FILE": str(tmp_path / "stub_state.json"),
        "GO2_STUB_TIME_SCALE": repr(TEST_TIME_SCALE),
        "GO2_STUB_DETECTIONS": json.dumps(detections or {}),
    })
    if fault:
        env["GO2_STUB_FAULT"] = fault
    env.update(extra)
    return env


def run_module(name: str, params: Any, env: dict[str, str],
               timeout: float = SKILL_RUN_TIMEOUT_S) -> subprocess.CompletedProcess:
    """``python -m go2_skills.<name> <params>``; params is a JSON-able value or a raw str."""
    arg = params if isinstance(params, str) else json.dumps(params)
    return subprocess.run([sys.executable, "-m", f"go2_skills.{name}", arg],
                          env=env, cwd=REPO_ROOT, capture_output=True, text=True,
                          timeout=timeout)


def single_response(proc: subprocess.CompletedProcess) -> dict:
    """Assert stdout is exactly one JSON line and return it parsed."""
    lines = proc.stdout.splitlines()
    assert len(lines) == 1, f"stdout={proc.stdout!r} stderr={proc.stderr[-2000:]!r}"
    assert proc.stdout.endswith("\n")
    return json.loads(lines[0])
