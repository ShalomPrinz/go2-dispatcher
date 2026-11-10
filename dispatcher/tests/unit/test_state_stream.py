"""The state stream hands samples and link changes to the observer without letting it break the stream."""

from __future__ import annotations

import json
import time

from dispatcher import robot
from dispatcher.robot import OBSERVER_FAILED_WARNING, StateStream
from dispatcher.tests.helpers.fakes import state
from skills.robot_ipc import LinkEvent, StateReply


class FakeClient:
    def __init__(self, events):
        self.events = list(events)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def subscribe(self, period_s):
        return "up"

    def next_event(self, wait_s):
        if self.events:
            return self.events.pop(0)
        time.sleep(0.01)
        return None


class RaisingObserver:
    def __init__(self):
        self.links: list[str] = []
        self.samples = 0

    def robot_sample(self, state, link, rx_mono):
        self.samples += 1
        raise RuntimeError("broken telemetry")

    def robot_link(self, link, rx_mono):
        self.links.append(link)


def test_raising_observer_does_not_break_the_stream(tmp_path, monkeypatch, capsys):
    events = [
        StateReply(state("standing"), 1.0, "up"),
        LinkEvent("down", 1.0),
        StateReply(state("standing"), 2.0, "up"),
    ]
    monkeypatch.setattr(robot.RobotClient, "connect", classmethod(lambda cls, path: FakeClient(events)))
    observer = RaisingObserver()
    path = tmp_path / "s.state_stream.jsonl"
    stream = StateStream("unused.sock", path, 0.0, observer)  # type: ignore[arg-type]
    deadline = time.monotonic() + 2.0
    while observer.samples < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    stream.close()
    types = [json.loads(line)["type"] for line in path.read_text(encoding="utf-8").splitlines()]
    assert types == ["stream_start", "sample", "link", "sample"]
    assert observer.samples == 2 and observer.links == ["up", "down"]
    assert capsys.readouterr().err.count(OBSERVER_FAILED_WARNING.format(exception_type="RuntimeError")) == 1
