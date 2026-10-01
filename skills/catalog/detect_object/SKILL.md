---
name: detect_object
entrypoint: skills.detect_object
description: Look through the front camera once and report whether an object is visible, where it is in the frame, and roughly how close it is. Does not move the robot.
params:
  target:
    type: string
    description: Object to look for, as a lowercase COCO class name (for example chair, person, bottle, cell phone).
---

# detect_object

Grabs one frame from the front camera and runs YOLO on it (weights from
`GO2_YOLO_WEIGHTS`; never downloaded automatically). Reports the most confident box of
the target class: `position` (left / center / right, by box centre) and `closeness`
(near / medium / far, by box height). Not seeing the object is a normal `ok` result with
`object_found: false`.

The target must be one of the 80 COCO classes in `skills/coco.py`; anything else
returns `unsupported_object` with the closest supported names. Camera and decoding
problems return `camera_unavailable`, `bad_frame` or `weights_missing`.

Manual run on the stub:

    GO2_BACKEND=stub GO2_STUB_DETECTIONS='{"chair":"center:near"}' \
      uv run python -m skills.detect_object '{"target":"chair"}'
