# Open decisions

The table from spec §20, kept current. v1 implements the stated default for each item, and each is designed to be a small change later. When an item is resolved, update its row here and add an entry to `docs/decisions.md`.

| ID | Question | Default implemented in v1 | Status |
|---|---|---|---|
| OD-1 | No skill can stand the robot up. After `sit`, motion fails until a person stands it up; in `batch`, one `sit` affects every later task. Add a `stand` skill (`StandUp()` then `BalanceStand()`, with a settle wait)? | Not added (the agreed skill set is the five skills). The model sees `Posture: sitting` and should ABORT with an explanation. Adding `stand` is one SKILL.md + one module; recommended before experiments. | Open |
| OD-2 | Posture rule for the real robot (`body_height` thresholds 0.15 / 0.22 m, or use `mode`). | Thresholds in `posture.py`; `mode` logged; decide after robot checklist items 1 and 7. | Open (needs the robot checklist) |
| OD-3 | Python version supported by `unitree_sdk2py` + `cyclonedds==0.10.2` on the lab machine. | `>=3.10,<3.12`, `tomli` fallback for 3.10. Pin exactly once the lab machine is set up. | Open (needs the lab machine) |
| OD-4 | What `Move()` does while lying down (non-zero code or silent no-op). If silent, `walk` reports `ok` while nothing moved. | Non-zero is an error; record actual behaviour (checklist item 8). | Open (needs the robot checklist) |
| OD-5 | `message` on a `PLAN` reply: log only, or also send to the operator as progress? | Logged only. | Open |
| OD-6 | `batch` carries previous task and posture across lines; experiments may need independent tasks. | Carry over. A later `--independent` flag can reset both per line. | Open |
| OD-7 | All (sketch) values: model `claude-sonnet-5-5` (with `thinking = "between_tools"`, `max_tokens` 2048, `tool_choice` auto, no `temperature`), horizon 5, failures 3, calls 20, time limit 300 s, K 10, budget 10 m / 720°, timeout formulas, walk 0.1–3.0 m, turn 5–180°, request timeout 60 s. | As listed, all in config or named constants. | Open (tune) |
| OD-8 | Unsupported `detect_object` target is a skill error (counts as a failure, costs a process start) rather than a bounds rejection. | Skill error with suggestions. | Open |
| OD-9 | Exact wording of the system text, notices, operator and transport messages. | Draft wording in §11; must be identical across experimental conditions. | Open |
| OD-10 | Settle waits after `StandDown` (3 s) and `Stretch` (6 s). | Fixed waits via `backend.sleep`; tune from robot checklist items 3 and 7. | Open (needs the robot checklist) |
| OD-11 | Bounds and motion-budget rejections count toward `max_failures` (following the professor's definition of failure: skill error, bounds rejection, timeout). If they should not, the call budget still bounds loops. | They count; `rejections` is logged separately in `task_end`. | Open |
| OD-12 | Stop word variants: exact `stop` was agreed; the implementation also accepts any case / surrounding spaces and the Telegram command `/stop`. | Accept these variants. Revert to exact match if unwanted. | Open |
| OD-13 | `max_llm_calls` is the same across horizon conditions; at horizon 1 long tasks may hit it, so `CALL_BUDGET_EXHAUSTED` rates partly reflect the cap. | One config value, logged per task; set it per condition when designing experiments. | Open |
| OD-14 | `uv lock` with the `robot` extra may fail on machines without CycloneDDS. | Try the extra first; fall back to documented manual install (§4.1). | Resolved on the dev machine: `uv lock` worked with the extra kept, so no fallback was needed (`docs/decisions.md`, T1). The fallback stays documented in `docs/setup.md`. |
