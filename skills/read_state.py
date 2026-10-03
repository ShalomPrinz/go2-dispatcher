"""Utility: sample robot state once (skills/docs/robot.md). Not a skill.

``python -m skills.read_state '{}'``. No faults, no watchdog.
"""

from __future__ import annotations

import sys
import time
import traceback

from skills import backend, result, stub
from skills.schema import ErrorCode

SKILL = "read_state"


def body(out: dict) -> None:
    stub.disable_faults()  # utilities ignore faults
    result.parse_params()
    backend.backend_name()  # raises BackendNotConfigured
    t = time.monotonic()
    try:
        out["state_after"] = backend.sample_state()
        out["status"] = "ok"
    except backend.BackendNotConfigured:
        raise
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        first = str(e).splitlines()[0] if str(e) else ""
        out.update(error_code=ErrorCode.STATE_UNAVAILABLE, error_message=f"{type(e).__name__}: {first}")
    out["timing"]["state_ms"] = result.ms_since(t)


def main() -> None:
    result.run_main(SKILL, body, watchdog=False)


if __name__ == "__main__":
    main()
