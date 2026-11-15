"""Skill runner: run_skill, the standard main() of a skill on the contract runtime (contract/docs/contract.md)."""

from __future__ import annotations

from collections.abc import Callable
from typing import NoReturn

from contract.policy import SkillPolicy
from contract.runtime import SkillOutcome, StateSampler, parse_params, run_main
from services.robot import client as robot_client

Body = Callable[[dict], SkillOutcome]


def run_skill(policy: SkillPolicy, body: Body, *, sample_state: bool = True) -> NoReturn:
    """Standard main() of a skill, via run_main:
    1. params = json.loads(argv[1]); must be a dict -> else error invalid_params
    2. if sample_state: sample state_before through the robot service (exception -> state_error, continue)
    3. outcome = body(params), a SkillOutcome
    4. if sample_state: sample state_after (exception -> appended to state_error)
    timing gets robot_connect_ms (if the process connected to the robot service), state_ms (time in the
    samples, if any was taken) and total_ms.
    Exceptions map as in SkillOutcome.from_exception; the traceback goes to stderr."""
    session = robot_client.session

    def main(states: StateSampler) -> SkillOutcome:
        params = parse_params()
        if sample_state:
            states.take("before")
        outcome = body(params)
        if sample_state:
            states.take("after")
        return outcome

    run_main(
        policy.name,
        main,
        states=StateSampler(session.read_state, prepare=session.client),
        setup=session.setup_timing,
    )
