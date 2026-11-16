"""The stub world's environment format and defaults, shared by the dispatcher, the robot service and the vision
service (contract/docs/contract.md): postures, conditions, the default state file and the objects in
``GO2_STUB_OBJECTS``. Standard library only."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, get_args

from contract.env import STUB_OBJECTS

# Used when the env is unset (manual runs) and as the dispatcher's stub config defaults.
DEFAULT_STATE_FILE = "logs/.stub_state.json"

StubPosture = Literal["standing", "sitting"]  # the postures the stub can hold; a subset of contract.schema.Posture
STUB_POSTURES: tuple[StubPosture, ...] = get_args(StubPosture)
DEFAULT_POSTURE: StubPosture = "standing"

Condition = Literal["fallen", "stuck"]  # robot faults the sim robot keeps in the state file
CONDITIONS: tuple[Condition, ...] = get_args(Condition)

# Object heights for GO2_STUB_OBJECTS entries without height_m.
DEFAULT_HEIGHTS_M = {"bottle": 0.25, "cup": 0.10, "chair": 0.90, "person": 1.70}


@dataclass(frozen=True)
class WorldObject:
    x: float
    y: float
    height_m: float


def _finite(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{where} must be a finite number, got {value!r}")
    return float(value)


def parse_objects(data: object) -> dict[str, WorldObject]:
    """Validate ``{class: {"x", "y", "height_m"?}}``; ``height_m`` defaults from ``DEFAULT_HEIGHTS_M``."""
    if not isinstance(data, Mapping):
        raise ValueError(f"stub objects must be an object of objects, got {data!r}")
    out: dict[str, WorldObject] = {}
    for name, spec in data.items():
        if not isinstance(spec, Mapping) or not {"x", "y"} <= set(spec) <= {"x", "y", "height_m"}:
            raise ValueError(f"stub object {name!r} must have x, y and optional height_m, got {spec!r}")
        if "height_m" in spec:
            height = _finite(spec["height_m"], f"stub object {name!r} height_m")
            if height <= 0:
                raise ValueError(f"stub object {name!r} height_m must be > 0, got {height!r}")
        elif name in DEFAULT_HEIGHTS_M:
            height = DEFAULT_HEIGHTS_M[name]
        else:
            raise ValueError(
                f"stub object {name!r} needs height_m (defaults exist only for {', '.join(DEFAULT_HEIGHTS_M)})"
            )
        out[name] = WorldObject(
            x=_finite(spec["x"], f"stub object {name!r} x"),
            y=_finite(spec["y"], f"stub object {name!r} y"),
            height_m=height,
        )
    return out


def objects() -> dict[str, WorldObject]:
    """The objects in ``GO2_STUB_OBJECTS``; none when unset."""
    raw = os.environ.get(STUB_OBJECTS)
    return parse_objects(json.loads(raw)) if raw else {}
