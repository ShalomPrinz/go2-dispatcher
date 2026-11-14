# robot/CLAUDE.md

The `robot` package: the robot service, its protocol and client, the lease, the real Go2 backend and the sim, the stub state file, fault injection, and the `stop_move` and `read_state` utilities. The contract it follows is the `contract` package ([contract/CLAUDE.md](../contract/CLAUDE.md)). Repo-wide rules are in the root [CLAUDE.md](../CLAUDE.md).

## Commands

```bash
uv run pytest robot -q                         # robot unit tests only
uv run pytest -q robot skills contract         # after touching the protocol, the client or the stub state: the skills use them
uv run pytest -m integration                   # after touching the service, a utility, the sim or fault injection
uv run go2 state                               # stub state as JSON, through the read_state utility
uv run go2 --reset-stub                        # stub checks: back to standing (sit persists across tasks)
uv run go2 --fault 1:hang run "…"              # stub fault injection: error, hang, crash, garbage, service_hang, service_crash, stop_error, rejected, fallen, stuck (needs ANTHROPIC_API_KEY)
uv run lint-imports                            # import contracts; must pass before a commit
```

## Docs

| Doc | Covers |
|---|---|
| [service.md](docs/service.md) | The package's modules and client surface, the robot service and its protocol, the lease, the client, the lifecycle contract, the stub backend, fault injection |
| [robot.md](docs/robot.md) | Robot facts, SDK and DDS usage, state sampling, posture rule, settle waits, real backend, supervised robot checklist |
| [contract.md](../contract/docs/contract.md) | Response schema, error codes, process environment, `process` and `backend`, wire framing |
| [skills.md](../skills/docs/skills.md) | The skills that use the service, running a skill by hand |
| [safety.md](../docs/safety.md) | Stop path, motion budget, bounds, supervised-operation rules |
| [configuration.md](../docs/configuration.md) | `robot.*` and `stub.*` config keys, `.env` |
| [setup.md](../docs/setup.md) | Installing the `robot` extra, network interface |
| [testing.md](../tests/docs/testing.md) | Test layout, markers, fault injection in tests, robot checks |

## Gotchas

- The stop path, the lease, `Motion`, yaw tracking, the fallback stop, the orphan watchdog and the single-instance lock are safety-critical: a change there needs its test and the owning doc in the same commit ([safety.md](../docs/safety.md#the-stop-path)).
- Real-backend code (`backends/real.py`) cannot be run from an agent: no `--backend real`, no `robot.backend = "real"`, no `--run-robot`. Verify it with unit tests and the stub only ([safety.md](../docs/safety.md#supervised-operation-rules)).
- Mark robot facts not yet checked on the robot as *unverified*. Checklist results go only in [robot.md](docs/robot.md#results), and only from a supervised session reported by a person.
- `skills` and `dispatcher` import only `robot.protocol`, `robot.client`, `robot.ramp`, `robot.faults` and `robot.stub_state`; these modules never import a backend, the service or a third-party package ([service.md](docs/service.md#the-package)).
- `robot` imports `contract` only. The one exception: `stop_move` and `read_state` import `skills.runner` ([service.md](docs/service.md#design-decisions)).
- Third-party imports (`unitree_sdk2py`) live inside functions in `backends/real.py`; importing any `robot` module needs only the standard library.
- The wire protocol carries `PROTOCOL_VERSION`; a message change is a protocol change, documented in [service.md](docs/service.md#robot-service) in the same commit.
- The dispatcher starts the service by `protocol.SERVICE_MODULE` and never imports `robot.service`.
