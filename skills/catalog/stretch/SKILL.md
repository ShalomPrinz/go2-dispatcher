---
name: stretch
entrypoint: skills.stretch
description: Perform the robot's built-in stretch routine.
expect:
  posture: standing
  mode: 0
requires:
  posture: [standing]
---

## Use when
The robot should stretch.

## Requires
The robot is standing. Fails while sitting.

## Effect
Runs the robot's built-in stretch routine in place.

## Limits
None.
