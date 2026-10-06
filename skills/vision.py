"""Turn a detection box into position and closeness, shared by the real and stub detectors
(skills/docs/robot.md). Standard library and skills only."""

from __future__ import annotations

from skills.backend import DetectResult

LEFT_MAX_X = 0.4  # box centre x fraction < this -> left (tunable)
RIGHT_MIN_X = 0.6  # > this -> right, else center (tunable)
NEAR_MIN_H = 0.6  # box height fraction > this -> near (tunable)
MEDIUM_MIN_H = 0.3  # > this -> medium, else far (tunable)
CONFIDENCE_DECIMALS = 2


def classify_box(cx_frac: float, h_frac: float, confidence: float) -> DetectResult:
    """A found result from a box's centre-x and height as fractions of the image width and height."""
    position = "left" if cx_frac < LEFT_MAX_X else "right" if cx_frac > RIGHT_MIN_X else "center"
    closeness = "near" if h_frac > NEAR_MIN_H else "medium" if h_frac > MEDIUM_MIN_H else "far"
    return DetectResult(
        found=True, position=position, closeness=closeness, confidence=round(confidence, CONFIDENCE_DECIMALS)
    )
