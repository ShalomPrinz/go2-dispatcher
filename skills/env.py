"""The skill process environment contract (skills/docs/skills.md): every ``GO2_*`` variable the
executor sets, and the builder that sets them. Standard library only."""

from __future__ import annotations

import json
from collections.abc import Mapping

BACKEND = "GO2_BACKEND"
IFACE = "GO2_IFACE"
YOLO_WEIGHTS = "GO2_YOLO_WEIGHTS"
STUB_STATE_FILE = "GO2_STUB_STATE_FILE"
STUB_TIME_SCALE = "GO2_STUB_TIME_SCALE"
STUB_DETECTIONS = "GO2_STUB_DETECTIONS"
STUB_FAULT = "GO2_STUB_FAULT"
PARENT_PID = "GO2_PARENT_PID"


def child_env(
    base: Mapping[str, str],
    *,
    backend: str,
    network_interface: str,
    yolo_weights: str,
    stub_state_file: str,
    stub_time_scale: float,
    stub_detections: Mapping[str, str],
    parent_pid: int,
    fault: str | None = None,
) -> dict[str, str]:
    """``base`` plus the contract variables; ``GO2_STUB_FAULT`` is set only from ``fault``, never inherited."""
    env = {k: v for k, v in base.items() if k != STUB_FAULT}
    env.update(
        {
            "PYTHONUNBUFFERED": "1",
            BACKEND: backend,
            IFACE: network_interface,
            YOLO_WEIGHTS: yolo_weights,
            STUB_STATE_FILE: stub_state_file,
            STUB_TIME_SCALE: repr(stub_time_scale),
            STUB_DETECTIONS: json.dumps(dict(stub_detections)),
            PARENT_PID: str(parent_pid),
        }
    )
    if fault:
        env[STUB_FAULT] = fault
    return env
