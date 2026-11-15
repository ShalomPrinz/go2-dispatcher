"""The stub fault file (contract/docs/contract.md)."""

from __future__ import annotations

import pytest

from contract import faults


def test_write_take_consumes_once(tmp_path):
    state = tmp_path / "stub_state.json"
    assert faults.take_fault(state) is None
    faults.write_fault(state, "stuck")
    assert faults.fault_file(state) == tmp_path / "stub_state.fault.json"
    assert faults.take_fault(state) == "stuck"
    assert faults.take_fault(state) is None
    assert list(tmp_path.iterdir()) == []


def test_write_rejects_skill_fault(tmp_path):
    with pytest.raises(ValueError, match="not a robot fault"):
        faults.write_fault(tmp_path / "s.json", "hang")  # pyright: ignore[reportArgumentType]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("raw", ["{not json", '{"kind": "hang"}', "[]"], ids=["json", "skill_kind", "shape"])
def test_take_bad_file_raises_and_removes(tmp_path, raw):
    state = tmp_path / "s.json"
    faults.fault_file(state).write_text(raw)
    with pytest.raises(faults.BadFaultFile):
        faults.take_fault(state)
    assert list(tmp_path.iterdir()) == []


def test_clear(tmp_path):
    state = tmp_path / "s.json"
    faults.clear_fault(state)
    faults.write_fault(state, "rejected")
    faults.clear_fault(state)
    assert faults.take_fault(state) is None
