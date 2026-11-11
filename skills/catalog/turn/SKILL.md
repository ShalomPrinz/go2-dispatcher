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
expect:
  yaw_delta:
    param: angle_deg
    direction_param: direction
    tolerance_deg: 10
requires:
  posture: [standing]
---

## Use when
Changing the robot's heading, for example to face something or to look around.

## Requires
The robot is standing. Fails while sitting.

## Effect
Turns in place at 1.0 rad/s until the measured heading has changed by the given angle, then stops. The position does not change.

## Limits
turned_deg reports the angle measured at the stop, which may differ from angle_deg by a few degrees, or be smaller if the turn hit its time limit. If the heading cannot be measured, the turn runs for the time the angle should take: closed_loop is false and turned_deg is null.
