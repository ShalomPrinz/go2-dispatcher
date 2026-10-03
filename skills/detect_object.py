"""Skill: look through the front camera once for a COCO object (skills/docs/skills.md). Never moves."""

from __future__ import annotations

import difflib
import time

from skills import backend, policy, runner
from skills.coco import COCO_CLASSES
from skills.schema import ErrorCode

SUGGESTIONS_N = 3
SUGGESTIONS_CUTOFF = 0.5
CONFIDENCE_DECIMALS = 2
TIMEOUT_S = 45.0  # YOLO load on CPU (tunable)

POLICY = policy.SkillPolicy(
    name="detect_object",
    timeout=TIMEOUT_S,
    context_observations=("object_found", "position", "closeness", "confidence"),
)


def unsupported_message(target: str) -> str:
    close = difflib.get_close_matches(target, COCO_CLASSES, n=SUGGESTIONS_N, cutoff=SUGGESTIONS_CUTOFF)
    if close:
        return f"'{target}' is not a detectable object. Closest supported: {', '.join(close)}."
    return f"'{target}' is not a detectable object."


def body(params: dict) -> runner.SkillOutcome:
    target = params["target"].strip().lower()
    obs: dict = {"target": target}
    if target not in COCO_CLASSES:
        return runner.SkillOutcome.error(
            ErrorCode.UNSUPPORTED_OBJECT, unsupported_message(target), observations=obs, timing={}
        )
    t = time.monotonic()
    detector = backend.get_detector()
    timing = {"init_ms": runner.ms_since(t)}
    t = time.monotonic()
    try:
        found = detector.detect(target)
    except backend.DetectorError as e:
        timing["exec_ms"] = runner.ms_since(t)
        return runner.SkillOutcome.error(e.code, f"{type(e).__name__}: {e}", observations=obs, timing=timing)
    timing["exec_ms"] = runner.ms_since(t)
    obs["object_found"] = bool(found.found)
    if found.found:
        obs.update(
            position=found.position,
            closeness=found.closeness,
            confidence=round(float(found.confidence), CONFIDENCE_DECIMALS),
        )
    return runner.SkillOutcome.ok(observations=obs, timing=timing)


def main() -> None:
    runner.run_skill(POLICY, body)


if __name__ == "__main__":
    main()
