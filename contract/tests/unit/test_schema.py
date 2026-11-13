"""The response schema's own rule: error if and only if status is error (contract/docs/contract.md)."""

from __future__ import annotations

import pytest

from contract.schema import SCHEMA_VERSION, SkillError, SkillResponse


def test_error_iff_status_error():
    with pytest.raises(ValueError):
        SkillResponse(schema_version=SCHEMA_VERSION, skill="walk", status="ok", error=SkillError("x", "y"))
    with pytest.raises(ValueError):
        SkillResponse(schema_version=SCHEMA_VERSION, skill="walk", status="error")
