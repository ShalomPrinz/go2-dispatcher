"""Utility: sample robot state once (docs/robot.md). Not a skill.

``python -m skills.read_state '{}'``. No faults, no watchdog.
"""

from __future__ import annotations

import os
import sys
import time
import traceback

from skills import backend, result

SKILL = "read_state"


def main() -> None:
    result.capture_stdout()
    t0 = time.monotonic()
    os.environ.pop("GO2_STUB_FAULT", None)  # utilities ignore faults
    timing: dict = {}
    state_after = None
    try:
        result.parse_params()
        backend.backend_name()  # raises BackendNotConfigured
        t = time.monotonic()
        try:
            state_after = backend.sample_state()
            status, code, msg = "ok", None, None
        except backend.BackendNotConfigured:
            raise
        except Exception as e:
            traceback.print_exc(file=sys.stderr)
            first = str(e).splitlines()[0] if str(e) else ""
            status, code, msg = "error", "state_unavailable", f"{type(e).__name__}: {first}"
        timing["state_ms"] = result.ms_since(t)
    except result.InvalidParams as e:
        status, code, msg = "error", "invalid_params", str(e)
    except Exception as e:
        if not isinstance(e, backend.BackendNotConfigured):
            traceback.print_exc(file=sys.stderr)
        code, msg = result.error_from_exception(e)
        status = "error"
    timing["total_ms"] = result.ms_since(t0)
    result.emit(SKILL, status, error_code=code, error_message=msg,
                state_after=state_after, timing=timing)


if __name__ == "__main__":
    main()
