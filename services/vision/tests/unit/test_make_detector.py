"""The vision service's detector selection (services/vision/docs/vision.md)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from contract.backend import BackendNotConfigured
from contract.env import BACKEND
from services.vision import service
from services.vision.detectors.stub import StubDetector


def test_stub_backend_gives_stub_detector(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(BACKEND, "stub")
    assert isinstance(service.make_detector(), StubDetector)


def test_unset_backend_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(BACKEND, raising=False)
    with pytest.raises(BackendNotConfigured, match="is not set"):
        service.make_detector()


def test_service_import_loads_no_detector() -> None:
    code = "import sys, services.vision.service; print(sorted(m for m in sys.modules if '.detectors.' in m))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == "[]"
