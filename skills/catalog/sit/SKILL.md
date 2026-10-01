---
name: sit
entrypoint: skills.sit
description: Lower the robot's body to the ground (sit or lie down).
---

# sit

Calls `StandDown` (as the original skill did, not `Sit`), then waits a fixed settle
time (`SitPolicy.SETTLE_S`) so the state sampled afterwards shows the finished posture.
Already lying down is not an error. Movement skills fail while the robot is down.

Manual run on the stub:

    GO2_BACKEND=stub uv run python -m skills.sit '{}'
