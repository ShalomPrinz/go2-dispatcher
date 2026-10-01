"""pytest plugin: shared options and marker gating, loaded with -p in pyproject.toml (docs/testing.md)."""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--run-live", action="store_true", default=False,
                     help="run live_llm tests (needs ANTHROPIC_API_KEY)")
    parser.addoption("--run-robot", action="store_true", default=False,
                     help="run robot tests (needs the real Go2)")
    parser.addoption("--update-golden", action="store_true", default=False,
                     help="rewrite golden files instead of comparing")


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
