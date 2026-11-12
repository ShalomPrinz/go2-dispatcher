"""Step verdicts: goal verification edges the stub tests do not reach (dispatcher/docs/loop-and-context.md)."""

from __future__ import annotations

import math

from dispatcher.models import Posture, RobotState, SkillError, SkillResponse
from dispatcher.verification import verify
from skills.manifest import Expectation, YawDelta
from skills.schema import SCHEMA_VERSION

TURN = Expectation(yaw_delta=YawDelta(param="angle_deg", direction_param="direction", tolerance_deg=10))
SIT = Expectation(posture="sitting")


def sample(yaw_deg: float = 0.0, *, posture: Posture = "standing", roll: float = 0.0, body_height: float | None = 0.3):
    return RobotState(
        t=0.0,
        backend="stub",
        posture=posture,
        mode=0,
        body_height=body_height,
        imu_rpy=[roll, 0.0, math.radians(yaw_deg)],
    )


def response(before=None, after=None, *, error: str | None = None, skill="turn") -> SkillResponse:
    return SkillResponse(
        schema_version=SCHEMA_VERSION,
        skill=skill,
        status="error" if error else "ok",
        error=SkillError(code=error, message="x") if error else None,
        state_before=before,
        state_after=after,
    )


def test_turn_yaw_delta_wraps_and_respects_direction():
    left_90 = {"direction": "left", "angle_deg": 90}
    across = response(sample(170), sample(-98))  # +92 deg through the +-180 seam
    assert verify(TURN, left_90, across, ok=True).verdict == "verified"
    assert verify(TURN, {"direction": "right", "angle_deg": 90}, across, ok=True).verdict == "failed"
    assert verify(TURN, left_90, response(sample(0), sample(75)), ok=True).verdict == "failed"  # 15 deg short


def test_missing_state_is_never_verified():
    left_90 = {"direction": "left", "angle_deg": 90}
    assert verify(TURN, left_90, response(None, sample(90)), ok=True).verdict == "unavailable"  # no state_before
    assert verify(None, {}, response(sample(), None, skill="walk"), ok=True).verdict == "unavailable"  # no fault check
    assert verify(None, {}, None, ok=True).verdict == "unavailable"  # no response
    assert (
        verify(SIT, {}, response(after=sample(posture="unknown", body_height=None)), ok=True).verdict == "unavailable"
    )
    assert verify(None, {}, response(after=sample(), skill="walk"), ok=True).verdict == "unverified"


def test_faults_override_the_goal_and_self_report_follows_status():
    fell = response(after=sample(posture="sitting", roll=math.pi / 2), skill="sit")
    assert verify(SIT, {}, fell, ok=True) == ("failed", ["fallen"])  # the posture rule calls a fallen robot sitting
    seen = Expectation(self_reported=True)
    assert verify(seen, {}, response(after=sample(), skill="detect_object"), ok=True).verdict == "verified"


def test_a_step_that_did_not_end_ok_gets_fault_detection_only():
    rejected = response(after=sample(posture="sitting", roll=math.pi / 2), error="sdk_error", skill="sit")
    assert verify(SIT, {}, rejected, ok=False) == (None, ["command_rejected", "fallen"])
    camera = response(after=sample(), error="camera_unavailable", skill="detect_object")
    assert verify(Expectation(self_reported=True), {}, camera, ok=False) == (None, [])
    assert verify(TURN, {"direction": "left", "angle_deg": 90}, None, ok=False) == (None, [])  # killed
