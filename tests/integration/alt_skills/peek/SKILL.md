---
name: peek
entrypoint: tests.integration.alt_skills.peek
description: Look through the front camera once for an object. Does not move the robot.
params:
  target:
    type: string
    description: Object to look for, as a lowercase COCO class name.
expect:
  self_reported: true
uses_vision: true
---

## Use when
Checking whether an object is in front of the robot.

## Requires
None.

## Effect
Reports whether the target is visible, and where.

## Limits
Sees only what is in front of the camera.
