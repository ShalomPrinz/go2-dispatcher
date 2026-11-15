---
name: spin
entrypoint: tests.integration.alt_skills.spin
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
expect:
  yaw_delta:
    param: angle_deg
    direction_param: direction
    tolerance_deg: 10
requires:
  posture: [standing]
moves: true
---

## Use when
Changing the robot's heading.

## Requires
The robot is standing.

## Effect
Turns in place by the given angle, then stops.

## Limits
The measured angle may differ by a few degrees.
