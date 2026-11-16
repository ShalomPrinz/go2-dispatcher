"""The child process environment, without secrets (contract/docs/contract.md, docs/configuration.md)."""

from __future__ import annotations

import os

from contract.env import child_env

from ..config import SECRET_ENV, Config


class ChildEnv:
    """Builds the environment of every process the dispatcher starts from ``os.environ`` at call time, minus the
    secrets, plus the contract variables from ``cfg``."""

    def __init__(self, cfg: Config):
        self._cfg = cfg

    def for_skill(self, fault: str | None, lease_token: str) -> dict[str, str]:
        """A skill process: its skill fault, if any, and the step's lease token."""
        return self._build(fault, lease_token)

    def for_service(self) -> dict[str, str]:
        """A service or utility process: no fault, no lease token."""
        return self._build(None, None)

    def _build(self, fault: str | None, lease_token: str | None) -> dict[str, str]:
        cfg = self._cfg
        return child_env(
            {k: v for k, v in os.environ.items() if k not in SECRET_ENV},
            backend=cfg.robot.backend,
            network_interface=cfg.robot.network_interface,
            yolo_weights=str(cfg.robot.yolo_weights),
            stub_state_file=str(cfg.stub.state_file),
            stub_time_scale=cfg.stub.time_scale,
            stub_objects=cfg.stub.objects,
            parent_pid=os.getpid(),
            log_dir=cfg.log.dir,
            fault=fault,
            lease_token=lease_token,
        )
