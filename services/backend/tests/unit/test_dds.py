"""DDS init once per process, with a fake SDK channel module (services/backend/docs/backend.md)."""

from __future__ import annotations

import sys
import types

import pytest

from contract.backend import BackendNotConfigured
from contract.env import IFACE
from services.backend import dds


@pytest.fixture
def fresh(monkeypatch):
    """A process that has not attempted DDS init, with a fake unitree_sdk2py channel module recording its calls."""
    monkeypatch.setattr(dds, "_dds_ready", False)
    monkeypatch.setattr(dds, "_dds_init_s", None)
    calls = []
    channel = types.ModuleType("unitree_sdk2py.core.channel")
    channel.ChannelFactoryInitialize = lambda domain, iface: calls.append((domain, iface))  # pyright: ignore[reportAttributeAccessIssue]
    for name in ("unitree_sdk2py", "unitree_sdk2py.core"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "unitree_sdk2py.core.channel", channel)
    return calls


def test_init_once_per_process_and_reports_its_time(fresh, monkeypatch):
    monkeypatch.setenv(IFACE, "eth0")
    assert dds.dds_init_ms() is None
    dds.init_dds()
    dds.init_dds()
    assert fresh == [(dds.DDS_DOMAIN_ID, "eth0")]
    assert isinstance(dds.dds_init_ms(), float)


def test_missing_iface_is_not_configured_and_not_attempted(fresh, monkeypatch):
    monkeypatch.delenv(IFACE, raising=False)
    with pytest.raises(BackendNotConfigured):
        dds.init_dds()
    assert fresh == [] and dds.dds_init_ms() is None
