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

## Use when
Changing the robot's heading, for example to face something or to look around.

## Requires
The robot is standing. Fails while sitting.

## Effect
Turns in place at 1.0 rad/s by the given angle, then stops. The position does not change.

## Limits
Open-loop: the robot turns for the time the angle should take; the angle actually turned is not measured.
