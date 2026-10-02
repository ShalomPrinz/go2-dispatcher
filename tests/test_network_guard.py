"""The default suite refuses outbound network access (tests/docs/testing.md)."""

from __future__ import annotations

import socket

import pytest

from tests.pytest_plugin import NetworkBlockedError


def test_outbound_connection_is_blocked() -> None:
    with pytest.raises(NetworkBlockedError, match="test_outbound_connection_is_blocked.*--run-live"):
        socket.create_connection(("api.anthropic.com", 443), timeout=1)
