"""Skill: look through the front camera once for a COCO object (skills/docs/skills.md). Never moves."""

from __future__ import annotations

import difflib
import time

from skills import backend, result
from skills.coco import COCO_CLASSES
from skills.policy_base import SkillPolicy

SKILL = "detect_object"
SUGGESTIONS_N = 3
SUGGESTIONS_CUTOFF = 0.5
CONFIDENCE_DECIMALS = 2


class DetectObjectPolicy(SkillPolicy):
    name = "detect_object"
    context_observations = ("object_found", "position", "closeness", "confidence")
    TIMEOUT_S = 45.0  # YOLO load on CPU (tunable)

    def timeout_s(self, p):
        return self.TIMEOUT_S


POLICY = DetectObjectPolicy()


def unsupported_message(target: str) -> str:
    close = difflib.get_close_matches(target, COCO_CLASSES, n=SUGGESTIONS_N, cutoff=SUGGESTIONS_CUTOFF)
    if close:
        return f"'{target}' is not a detectable object. Closest supported: {', '.join(close)}."
    return f"'{target}' is not a detectable object."


def body(params: dict):
    raw = params.get("target")
    if not isinstance(raw, str) or not raw.strip():
        raise result.InvalidParams("'target' must be a non-empty string")
    target = raw.strip().lower()
    obs: dict = {"target": target}
    if target not in COCO_CLASSES:
        return "error", obs, "unsupported_object", unsupported_message(target), {}
    t = time.monotonic()
    detector = backend.get_detector()
    timing = {"init_ms": result.ms_since(t)}
    t = time.monotonic()
    try:
        found = detector.detect(target)
    except backend.DetectorError as e:
        timing["exec_ms"] = result.ms_since(t)
        return "error", obs, e.code, f"{type(e).__name__}: {e}", timing
    timing["exec_ms"] = result.ms_since(t)
    obs["object_found"] = bool(found.found)
    if found.found:
        obs.update(
            position=found.position,
            closeness=found.closeness,
            confidence=round(float(found.confidence), CONFIDENCE_DECIMALS),
        )
    return "ok", obs, None, None, timing


def main() -> None:
    result.run_skill(SKILL, body)


if __name__ == "__main__":
    main()
