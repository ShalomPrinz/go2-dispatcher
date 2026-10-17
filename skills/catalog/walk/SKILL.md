---
name: walk
entrypoint: skills.walk
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

## Use when
Moving the robot a set distance in a straight line without changing its heading.

## Requires
The robot is standing. Fails while lying down.

## Effect
Moves forward, backward, or sideways at 0.3 m/s, then stops. Left and right are sideways steps; the heading does not change.

## Limits
Open-loop: the robot moves for the time the distance should take; the distance actually covered is not measured.
