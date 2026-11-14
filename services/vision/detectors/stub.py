"""The stub's front camera (services/vision/docs/vision.md): a pinhole camera over the objects placed in the stub world,
at the pose in the stub state file."""

from __future__ import annotations

import math

from contract.backend import stub_sleep
from services.robot.stub_state import Pose, WorldObject, objects, read_state
from services.vision import boxes
from services.vision.protocol import DetectResult

DETECT_S = 0.5

# Simulated front camera (services/vision/docs/vision.md): Unitree Go2 spec 1280x720, 120 deg FOV, taken as horizontal.
CAMERA_WIDTH_PX = 1280  # (tunable)
CAMERA_HEIGHT_PX = 720  # (tunable)
CAMERA_HFOV_DEG = 120.0  # (tunable)
CAMERA_OFFSET_M = 0.3  # camera ahead of the body centre (tunable)
CAMERA_FOCAL_PX = CAMERA_WIDTH_PX / 2 / math.tan(math.radians(CAMERA_HFOV_DEG) / 2)  # square pixels
MIN_BOX_H = 0.02  # smaller boxes are not detected (tunable)
CONFIDENCE_MIN = 0.6  # at box height 0 (tunable)
CONFIDENCE_MAX = 0.95  # at box height 1 (tunable)


def project(pose: Pose, obj: WorldObject) -> DetectResult:
    """What the front camera at ``pose`` sees of ``obj``: a pinhole box classified like the real detector's."""
    cam_x = pose.x + CAMERA_OFFSET_M * math.cos(pose.yaw)
    cam_y = pose.y + CAMERA_OFFSET_M * math.sin(pose.yaw)
    dx, dy = obj.x - cam_x, obj.y - cam_y
    depth = dx * math.cos(pose.yaw) + dy * math.sin(pose.yaw)
    left = -dx * math.sin(pose.yaw) + dy * math.cos(pose.yaw)
    if depth <= 0 or abs(math.atan2(left, depth)) > math.radians(CAMERA_HFOV_DEG) / 2:
        return DetectResult(found=False)
    h_frac = min(1.0, CAMERA_FOCAL_PX * obj.height_m / depth / CAMERA_HEIGHT_PX)
    if h_frac < MIN_BOX_H:
        return DetectResult(found=False)
    cx_frac = 0.5 - CAMERA_FOCAL_PX * (left / depth) / CAMERA_WIDTH_PX  # positive bearing (left) -> small x
    return boxes.classify_box(cx_frac, h_frac, CONFIDENCE_MIN + (CONFIDENCE_MAX - CONFIDENCE_MIN) * h_frac)


class StubDetector:
    """The vision service's stub camera; faults are applied in the skill process (inject_fault)."""

    def warm_up(self) -> None:
        pass

    def detect(self, target: str) -> DetectResult:
        stub_sleep(DETECT_S)
        obj = objects().get(target)
        return DetectResult(found=False) if obj is None else project(read_state().pose, obj)
