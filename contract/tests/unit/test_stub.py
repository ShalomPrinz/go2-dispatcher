"""The stub world's objects format (contract/docs/contract.md)."""

from __future__ import annotations

import math

import pytest

from contract import stub


def test_parse_objects_defaults_and_errors():
    parsed = stub.parse_objects({"bottle": {"x": 1, "y": 0}, "car": {"x": 5, "y": 1, "height_m": 1.5}})
    assert parsed == {"bottle": stub.WorldObject(1.0, 0.0, 0.25), "car": stub.WorldObject(5.0, 1.0, 1.5)}
    with pytest.raises(ValueError, match="'car' needs height_m"):
        stub.parse_objects({"car": {"x": 5, "y": 1}})
    bad_objects = (
        {"chair": {"x": 1}},
        {"chair": {"x": 1, "y": 0, "z": 0}},
        {"chair": {"x": "1", "y": 0}},
        {"chair": {"x": math.nan, "y": 0}},
        {"car": {"x": 5, "y": 1, "height_m": 0}},
        [],
    )
    for bad in bad_objects:
        with pytest.raises(ValueError):
            stub.parse_objects(bad)
