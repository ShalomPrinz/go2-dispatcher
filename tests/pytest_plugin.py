"""pytest plugin: shared options and marker gating, loaded with -p in pyproject.toml (tests/docs/testing.md)."""

from __future__ import annotations

import ipaddress
import socket

import pytest

# what socket.connect accepts: a (host, port, ...) tuple or a Unix socket path
_Address = tuple[object, ...] | str | bytes

NETWORK_MARKERS = ("live_llm", "robot")  # opted in by --run-live / --run-robot; skipped without the flag


class NetworkBlockedError(RuntimeError):
    """Raised when a default-suite test opens a non-loopback connection or resolves a non-local name."""


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live", action="store_true", default=False, help="run live_llm tests (needs ANTHROPIC_API_KEY)"
    )
    parser.addoption("--run-robot", action="store_true", default=False, help="run robot tests (needs the real Go2)")
    parser.addoption(
        "--update-golden", action="store_true", default=False, help="rewrite golden files instead of comparing"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    skip_live = pytest.mark.skip(reason="needs --run-live")
    skip_robot = pytest.mark.skip(reason="needs --run-robot")
    for item in items:
        if item.get_closest_marker("live_llm") and not config.getoption("--run-live"):
            item.add_marker(skip_live)
        if item.get_closest_marker("robot") and not config.getoption("--run-robot"):
            item.add_marker(skip_robot)


@pytest.fixture
def update_golden(request: pytest.FixtureRequest) -> bool:
    """True when golden files should be rewritten instead of compared."""
    return bool(request.config.getoption("--update-golden"))


def _is_local(host: object) -> bool:
    if isinstance(host, bytes):
        host = host.decode()
    if host is None or host in ("", "localhost"):
        return True
    try:
        return ipaddress.ip_address(str(host).split("%")[0]).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def _block_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse outbound connections and DNS lookups; loopback and Unix sockets stay allowed (tests/docs/testing.md)."""
    if any(request.node.get_closest_marker(m) for m in NETWORK_MARKERS):
        return
    real_connect, real_connect_ex, real_getaddrinfo = (
        socket.socket.connect,
        socket.socket.connect_ex,
        socket.getaddrinfo,
    )

    def check(sock: socket.socket | None, target: object) -> None:
        inet = sock is None or sock.family in (socket.AF_INET, socket.AF_INET6)
        host = target[0] if isinstance(target, tuple) else target
        if inet and not _is_local(host):
            raise NetworkBlockedError(
                f"{request.node.nodeid}: network access to {target!r} is blocked in the default suite; "
                "mark the test live_llm (--run-live) or robot (--run-robot) (tests/docs/testing.md)"
            )

    def connect(self: socket.socket, address: _Address) -> None:
        check(self, address)
        return real_connect(self, address)

    def connect_ex(self: socket.socket, address: _Address) -> int:
        check(self, address)
        return real_connect_ex(self, address)

    def getaddrinfo(
        host: bytes | str | None,
        port: bytes | str | int | None,
        family: int = 0,
        type: int = 0,
        proto: int = 0,
        flags: int = 0,
    ) -> list:
        check(None, host)
        return real_getaddrinfo(host, port, family, type, proto, flags)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
