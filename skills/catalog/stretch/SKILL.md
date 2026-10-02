---
name: stretch
entrypoint: skills.stretch
description: Perform the robot's built-in stretch routine.
---

# stretch

Calls the built-in `Stretch` routine, then waits a fixed settle time
(`SETTLE_S` in `skills/stretch.py`) for the routine to finish. Requires the robot to be standing;
otherwise the SDK call fails and the skill reports `sdk_error`.

Manual run on the stub:

    GO2_BACKEND=stub uv run python -m skills.stretch '{}'
