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
        backend.check()
    with pytest.raises(backend.BackendNotConfigured):
        backend.select({"real": 1, "stub": 2})


def test_select(monkeypatch):
    monkeypatch.setenv(env.BACKEND, "stub")
    assert backend.select({"real": 1, "stub": 2}) == 2
    with pytest.raises(backend.BackendNotConfigured, match="'stub' is not one of real"):
        backend.select({"real": 1})
