"""make_config: test config rooted at the repo, writing under tmp_path (docs/testing.md)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dispatcher.config import Config, build_config

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_TIME_SCALE = 0.01


def _merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "detections":
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def make_config(tmp_path: Path, **overrides: Any) -> Config:
    """Config with log.dir and stub.state_file under ``tmp_path``, stub.time_scale
    0.01, base dir = repo root. ``overrides`` are section dicts merged in, e.g.
    ``make_config(tmp_path, loop={"max_failures": 1}, stub={"faults": [...]})``."""
    data: dict[str, Any] = {
        "log": {"dir": str(tmp_path / "runs")},
        "stub": {
            "state_file": str(tmp_path / "runs" / ".stub_state.json"),
            "time_scale": TEST_TIME_SCALE,
        },
    }
    return build_config(_merge(data, overrides), REPO_ROOT)
