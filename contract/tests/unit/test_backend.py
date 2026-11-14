"""Backend selection (contract/docs/contract.md)."""

from __future__ import annotations

import pytest

from contract import backend, env


@pytest.mark.parametrize("value", [None, "", "simulator"], ids=["unset", "empty", "unknown"])
def test_backend_not_configured(monkeypatch, value):
    monkeypatch.delenv(env.BACKEND, raising=False)
    if value is not None:
        monkeypatch.setenv(env.BACKEND, value)
    with pytest.raises(backend.BackendNotConfigured):
        backend.backend_name()
