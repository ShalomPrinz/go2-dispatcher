---
name: detect_object
entrypoint: skills.detect_object
description: Look through the front camera once and report whether an object is visible, where it is in the frame, and roughly how close it is. Does not move the robot.
params:
  target:
    type: string
    description: Object to look for, as a lowercase COCO class name (for example chair, person, bottle, cell phone).
---

## Use when
Checking whether an object is in front of the robot, where it is, and roughly how close.

## Requires
None.

## Effect
Reports whether the target is visible and, if it is, its position in the frame (left, center or right) and its closeness (near, medium or far). Not seeing the target is a normal result, not an error.

## Limits
Sees only what is in front of the robot; turn to look elsewhere. The target must be a COCO class name; any other name fails with the closest supported names. Closeness is a rough estimate from the object's size in the image.
