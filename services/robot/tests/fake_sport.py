"""A scripted sport client for the robot service's motion loop and the stop_move utility
(services/robot/docs/service.md)."""

from __future__ import annotations


class FakeClient:
    """Sport client returning scripted codes. ``move`` items: int (returned) or an
    exception instance (raised); after the list runs out Move returns 0."""

    def __init__(self, move=(), stop=0, stand_down=0):
        self.move = list(move)
        self.stop = stop
        self.stand_down = stand_down
        self.calls: list[str] = []

    def Move(self, vx, vy, vyaw):
        self.calls.append("Move")
        item = self.move.pop(0) if self.move else 0
        if isinstance(item, BaseException):
            raise item
        return item

    def StopMove(self):
        self.calls.append("StopMove")
        if isinstance(self.stop, BaseException):
            raise self.stop
        return self.stop

    def StandDown(self):
        self.calls.append("StandDown")
        return self.stand_down

    def Stretch(self):
        self.calls.append("Stretch")
        return 0
