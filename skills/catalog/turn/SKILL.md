---
name: turn
entrypoint: skills.turn
description: Turn in place to the left or right by an angle, then stop.
params:
  direction:
    type: enum
    values: [left, right]
    description: Which way to turn.
  angle_deg:
    type: number
    min: 5
    max: 180
    default: 45
    unit: degrees
    description: Angle to turn.
---

# turn

Turns in place at a constant yaw rate of 1.0 rad/s, sending `Move(0, 0, ±rate)` at
10 Hz for `radians(angle_deg) / 1.0` seconds, then sends `StopMove`. The interface is in
degrees; the skill converts to radians. Open-loop, like `walk`.

Manual run on the stub:

    GO2_BACKEND=stub uv run python -m skills.turn '{"direction":"left","angle_deg":90}'
