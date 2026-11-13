"""Domain constants and small types that more than one package reads (contract/docs/contract.md).
Standard library only."""

from __future__ import annotations

from typing import Literal

YAW_SIGN = {"left": 1, "right": -1}  # turn direction to the sign of yaw_rad and vyaw (CCW +)

Link = Literal["up", "down"]  # whether robot state messages are arriving (skills/docs/robot.md)
