"""DDS init, once per process (services/backend/docs/backend.md): shared by the real robot backend and the real
detector. The SDK import is inside init_dds, so importing this module needs only the standard library."""

from __future__ import annotations

import os
import threading
import time

from contract.backend import BackendNotConfigured
from contract.env import IFACE

DDS_DOMAIN_ID = 0

_dds_ready = False
_dds_lock = threading.Lock()
_dds_init_s: float | None = None  # time in the channel import and ChannelFactoryInitialize, failed attempts included


def init_dds() -> None:
    """ChannelFactoryInitialize once per process; its time is reported as dds_init_ms."""
    global _dds_ready, _dds_init_s
    with _dds_lock:
        if _dds_ready:
            return
        iface = os.environ.get(IFACE, "")
        if not iface:
            raise BackendNotConfigured(f"{IFACE} is not set (robot.network_interface)")
        t = time.monotonic()
        try:
            from unitree_sdk2py.core.channel import ChannelFactoryInitialize

            ChannelFactoryInitialize(DDS_DOMAIN_ID, iface)
        finally:
            _dds_init_s = (_dds_init_s or 0.0) + time.monotonic() - t
        _dds_ready = True


def dds_init_ms() -> float | None:
    """Time spent in init_dds in this process, or None if it was never attempted."""
    return None if _dds_init_s is None else round(_dds_init_s * 1000.0, 3)
