"""Run one command under an armed robot service lease, as the dispatcher runs a step (skills/docs/skills.md).
For running a motion skill by hand: ``python -m skills.lease <timeout_s> <command> [args...]``."""

from __future__ import annotations

import os
import secrets
import subprocess
import sys

from skills import robot_ipc
from skills.env import LEASE_TOKEN


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit("usage: python -m skills.lease <timeout_s> <command> [args...]")
    timeout_s, command = float(sys.argv[1]), sys.argv[2:]
    token = secrets.token_hex(16)
    client = robot_ipc.RobotClient.connect(robot_ipc.socket_path(), control=True)
    try:
        client.arm(token, timeout_s)
        result = subprocess.run(command, env={**os.environ, LEASE_TOKEN: token}, check=False)
    finally:
        client.close()  # closing the control connection ends the lease and sends StopMove
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
