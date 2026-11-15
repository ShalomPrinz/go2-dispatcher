# services/robot/CLAUDE.md

The `robot` package: the robot service, its protocol and client, the lease, the real Go2 backend and the sim, fault injection, and the `stop_move` and `read_state` utilities. The contract it follows is the `contract` package ([contract/CLAUDE.md](../../contract/CLAUDE.md)). Repo-wide rules are in the root [CLAUDE.md](../../CLAUDE.md).

## Commands

```bash
uv run pytest services/robot -q                        # robot unit tests only
uv run pytest -q services/robot skills contract        # after touching the protocol or the client: the skills use them
uv run pytest -m integration                           # after touching the service, a utility, the sim or fault injection
uv run go2 state                                       # stub state as JSON, through the read_state utility
uv run go2 --reset-stub                                # stub checks: back to standing (sit persists across tasks)
uv run go2 --fault 1:hang run "…"                      # stub fault injection: error, hang, crash, garbage, service_hang, service_crash, stop_error, rejected, fallen, stuck (needs ANTHROPIC_API_KEY)
uv run lint-imports                                    # import contracts; must pass before a commit
```

## Docs

| Doc | Covers |
|---|---|
| [service.md](docs/service.md) | The package's modules and client surface, the robot service and its protocol, the lease, the client, the lifecycle contract, the stub backend, fault injection |
| [backend.md](../backend/docs/backend.md) | The stub state file format and writers, DDS init and `dds_init_ms` |
| [robot.md](docs/robot.md) | Robot facts, SDK and DDS usage, state sampling, posture rule, settle waits, real backend, supervised robot checklist |
| [contract.md](../../contract/docs/contract.md) | Response schema, error codes, process environment, `process` and `backend`, wire framing, the process runtime (`runtime`), the fault kinds (`SKILL_FAULTS`, `ROBOT_FAULTS`), the stub world format (`stub`) |
| [skills.md](../../skills/docs/skills.md) | The skills that use the service, running a skill by hand |
| [safety.md](../../docs/safety.md) | Stop path, motion budget, bounds, supervised-operation rules |
| [configuration.md](../../docs/configuration.md) | `robot.*` and `stub.*` config keys, `.env` |
| [setup.md](../../docs/setup.md) | Installing the `robot` extra, network interface |
| [testing.md](../../tests/docs/testing.md) | Test layout, markers, fault injection in tests, robot checks |

## Gotchas

- The stop path, the lease, `Motion`, yaw tracking, the fallback stop, the orphan watchdog and the single-instance lock are safety-critical: a change there needs its test and the owning doc in the same commit ([safety.md](../../docs/safety.md#the-stop-path)).
- Real-backend code (`backends/real.py`) cannot be run from an agent: no `--backend real`, no `robot.backend = "real"`, no `--run-robot`. Verify it with unit tests and the stub only ([safety.md](../../docs/safety.md#supervised-operation-rules)).
- Mark robot facts not yet checked on the robot as *unverified*. Checklist results go only in [robot.md](docs/robot.md#results), and only from a supervised session reported by a person.
- `skills` and `dispatcher` import only `services.robot.protocol`, `services.robot.client`, `services.robot.ramp`, `services.robot.faults`, and the dispatcher also `services.robot.stub` (the stub reset; skills never import it); these modules never import a backend, the service or a third-party package. The exception: the dispatcher also imports `services.robot.stop_move` and `services.robot.read_state`, which loads the backend interface `services.robot.backends` (no backend module) into its process ([service.md](docs/service.md#the-package)).
- `robot` imports `contract` and `services.backend` only; the stub state file and DDS init are `services.backend` ([backend/CLAUDE.md](../backend/CLAUDE.md)). `stop_move` and `read_state` run on `contract.runtime`; the fault kinds and the stub world format are `contract.faults` and `contract.stub` ([contract.md](../../contract/docs/contract.md#the-package)).
- The service and the utilities reach the robot only through `backends.load()`; never branch on the backend name in `robot` ([service.md](docs/service.md#robot-backends)).
- Third-party imports (`unitree_sdk2py`) live inside functions in `backends/real.py`; importing any `robot` module needs only the standard library.
- The wire protocol carries `PROTOCOL_VERSION`; a message change is a protocol change, documented in [service.md](docs/service.md#robot-service) in the same commit.
- The dispatcher starts the service by `protocol.SERVICE_MODULE` and never imports `services.robot.service`.
