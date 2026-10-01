---
name: walk
entrypoint: go2_skills.walk
description: Walk in a straight line forward, backward, or sideways by a distance, then stop.
params:
  direction:
    type: enum
    values: [forward, backward, left, right]
    description: Direction of travel relative to the robot's current heading.
  distance_m:
    type: number
    min: 0.1
    max: 3.0
    default: 0.9
    unit: metres
    description: Distance to travel.
---

# walk

Walks in a straight line at a constant 0.3 m/s, sending `Move` at 10 Hz for
`distance_m / 0.3` seconds, then sends `StopMove`. Left and right are sideways steps
(no turning). The distance is open-loop: the robot is commanded for the time it should
take, not measured.

Fails with `sdk_error` if any SDK call returns non-zero (for example walking while the
robot is lying down). Manual run on the stub:

    GO2_BACKEND=stub uv run python -m go2_skills.walk '{"direction":"forward","distance_m":1}'
