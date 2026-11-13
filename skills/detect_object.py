"""Skill: look through the front camera once for a COCO object (skills/docs/skills.md). Never moves."""

from __future__ import annotations

import difflib
import time

from contract import policy
from contract.schema import ErrorCode
from skills import backend, runner, stub, vision_ipc
from skills.coco import COCO_CLASSES
from skills.motion import PROCESS_OVERHEAD_S

SUGGESTIONS_N = 3
SUGGESTIONS_CUTOFF = 0.5
CONFIDENCE_DECIMALS = 2
REPLY_WAIT_S = 5.0  # vision service reply: one camera frame (CAMERA_TIMEOUT_S 3 s) + inference (tunable)
TIMEOUT_S = PROCESS_OVERHEAD_S + REPLY_WAIT_S  # the detector is loaded by the vision service, not here

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
    try:
        conn = vision_ipc.connect(vision_ipc.socket_path())
    except backend.DetectorError as e:
        timing = {"connect_ms": runner.ms_since(t)}
        return runner.SkillOutcome.error(e.code, vision_ipc.error_message(e), observations=obs, timing=timing)
    timing = {"connect_ms": runner.ms_since(t)}
    t = time.monotonic()
    try:
        with conn:
            if backend.backend_name() == "stub" and stub.inject_fault() == "error":
                raise backend.CameraUnavailable("injected camera fault")
            found = vision_ipc.detect(conn, target, REPLY_WAIT_S)
    except backend.DetectorError as e:
        timing["exec_ms"] = runner.ms_since(t)
        return runner.SkillOutcome.error(e.code, vision_ipc.error_message(e), observations=obs, timing=timing)
    timing["exec_ms"] = runner.ms_since(t)
    obs["object_found"] = bool(found.found)
    if found.found and found.confidence is not None:  # vision_ipc.parse_reply checked the fields
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
