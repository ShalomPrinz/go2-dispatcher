"""Telegram operator HTML: outcome text, escaping, trimming, step reasons (docs/running.md)."""

from __future__ import annotations

import re
from html.parser import HTMLParser

import pytest

from dispatcher import prompts
from dispatcher.models import StepDispatch, StepOutcome, StepRef, StepResult, TaskOutcome, TaskOutcomeCode
from dispatcher.registry import Registry
from dispatcher.transports.telegram_text import MAX_CHARS, format_outcome, step_reason, trim
from skills.policy import MotionCost
from skills.schema import ErrorCode
from tests.helpers import REPO_ROOT


@pytest.fixture(scope="module")
def registry():
    return Registry.load(REPO_ROOT / "skills" / "catalog")


REF = StepRef(call_index=1, plan_step=1, skill="sit", params={})


def step(index: int | None, outcome: StepOutcome = "ok", code: str | None = None, msg: str | None = None):
    dispatch = StepDispatch(index=index, timeout_s=10.0, motion_cost=MotionCost()) if index is not None else None
    return StepResult(ref=REF, dispatch=dispatch, outcome=outcome, error_code=code, error_message=msg)


def outcome(steps, failures=0, code: TaskOutcomeCode = "DONE", message="All done.", stop_move_failed=False):
    return TaskOutcome(
        run_id="r",
        task="t",
        outcome=code,
        message=message,
        steps=steps,
        llm_calls=1,
        failures=failures,
        stop_move_failed=stop_move_failed,
        duration_ms=1.0,
        final_posture="standing",
        log_path="",
    )


class _Balanced(HTMLParser):
    """Fails on a closing tag without its opening tag; ``open`` holds tags left open."""

    def __init__(self):
        super().__init__()
        self.open: list[str] = []

    def handle_starttag(self, tag, attrs):
        self.open.append(tag)

    def handle_endtag(self, tag):
        assert self.open and self.open.pop() == tag


def assert_balanced(text: str) -> None:
    parser = _Balanced()
    parser.feed(text)
    assert parser.open == []


def test_outcome_hides_error_detail(registry):
    rejected = step(None, "rejected", "bounds", "missing parameter 'direction' for skill walk")
    failed = step(2, "error", "motion_rejected", "Move returned 3104")
    text = format_outcome(outcome([step(1), rejected, failed], failures=2), registry)
    assert text.splitlines() == [
        "✅ <b>Done</b>: All done.",
        "Steps: 2 run, 2 failed",
        "1. ✅ sit() -&gt; ok",
        "– ❌ sit() was in a malformed plan",
        "2. ❌ sit() failed: the robot refused the move",
    ]
    assert "3104" not in text and "missing parameter" not in text


def test_outcome_categories_without_detail(registry):
    lost = outcome([step(1, "error", "sdk_error", "Traceback ...")], failures=3, code="FAILURE_BUDGET_EXHAUSTED")
    assert format_outcome(lost, registry).splitlines()[0] == (
        "❌ <b>Stopped</b>: the robot failed 3 times while trying this task. "
        "Last failure: sit failed: robot command error."
    )
    llm = outcome([], code="LLM_ERROR", message="Stopped: the model could not be reached (HTTP 529 overloaded).")
    assert "529" not in format_outcome(llm, registry)
    warned = format_outcome(outcome([], code="STOP_FAILED", message="x", stop_move_failed=True), registry)
    assert warned.splitlines()[1] == prompts.TELEGRAM_STOP_MOVE_WARNING


def test_model_text_is_escaped(registry):
    text = format_outcome(outcome([], message="<b>x</b> & <script>"), registry)
    assert text.splitlines()[0] == "✅ <b>Done</b>: &lt;b&gt;x&lt;/b&gt; &amp; &lt;script&gt;"


def test_every_error_code_has_a_reason():
    for code in ErrorCode:
        assert step_reason(step(1, "error", code.value)) == prompts.STEP_REASONS[code.value]
    assert step_reason(step(1, "error", "blocked")) == "failed: blocked"


def test_trim_drops_whole_lines_and_keeps_markup_balanced(registry):
    steps = [step(i, "error", "x" * 150, "y" * 150) for i in range(1, 80)]
    text = format_outcome(outcome(steps, failures=79, message="<i>" * 200), registry)
    assert len(text) <= MAX_CHARS
    assert_balanced(text)
    lines = text.splitlines()
    m = re.fullmatch(r"\((\d+) earlier lines omitted\)", lines[2])
    assert m is not None
    assert lines[3].startswith(f"{int(m.group(1)) + 1}. ❌ sit()") and lines[-1].startswith("79. ")
    one = trim(["<b>head</b>"], ["<i>a</i>" * 1000], ["tail"])  # one line is always kept
    assert one.splitlines()[1] == "<i>a</i>" * 1000
