---
name: stretch
entrypoint: go2_skills.stretch
description: Perform the robot's built-in stretch routine.
---

# stretch

Calls the built-in `Stretch` routine, then waits a fixed settle time
(`StretchPolicy.SETTLE_S`) for the routine to finish. Requires the robot to be standing;
otherwise the SDK call fails and the skill reports `sdk_error`.

Manual run on the stub:

    GO2_BACKEND=stub uv run python -m go2_skills.stretch '{}'
