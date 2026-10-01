# Go2 LLM Dispatcher v1 — Part 4 — Tests, docs, decisions

This is one part of the Go2 LLM Dispatcher v1 specification. The four parts together are the complete, normative spec. Section numbers (§) are global across the four files, so a reference like §14.4 may point to another part.

| File | Contents |
|---|---|
| `docs/spec/01-foundations.md` | §1–§6: scope, overview, glossary, repository layout, configuration, data models |
| `docs/spec/02-skills-and-robot.md` | §7–§10: skill contract, the five skills and two utilities, real and stub backends, registry and catalog |
| `docs/spec/03-dispatcher.md` | §11–§18: context, LLM client, validation and budgets, executor and stop path, dispatcher loop, transports, run log, failure handling |
| `docs/spec/04-tests-docs-decisions.md` | §19–§23 and Appendix A: tests, open decisions, documentation, build order, v2 seams and future ideas, design review answers |

Conventions (apply to all parts):

- **(sketch)** marks a starting value expected to be tuned. Implement every such value as a config key or a named class/module constant — never inline.
- "Must", "never", "always" are requirements. Everything else is guidance.
- If two parts of the spec seem to conflict, the more specific section wins. §20 (Open decisions) states the default to implement for each open item. Appendix A records the answers to the design review and is normative.

---

## 19. Tests

`pytest` + `pytest-timeout`. `uv run pytest` must pass with only core + dev dependencies, **no network, no API key, no robot, no SDK**.

Options in `tests/conftest.py`: `--run-live` (enables `live_llm`), `--run-robot` (enables `robot`), `--update-golden` (rewrites golden files instead of comparing).

### 19.1 Helpers (`tests/helpers/`)

- `make_config(tmp_path, **overrides)` — config with `log.dir` and `stub.state_file` under `tmp_path`, `stub.time_scale = 0.01`, base dir = repo root (so `skills/` loads).
- `ScriptedPlanner(items, on_call=None)` — implements `PlannerClient`. Items are returned in order: a `Plan` → valid `LLMResult`; a `dict` → treated as raw tool input and validated with `validate_tool_input`; an exception instance → raised. Records every call's kwargs. `on_call(call_index)` hook runs before returning (used to set the stop event mid-call). Raises `AssertionError` if called more times than scripted. Returns `usage={"input_tokens": 100, "output_tokens": 20}`.
- `FakeClock` — `now()`; `advance(seconds)`.
- `FakeExecutor(results)` — returns scripted `ExecResult`s; records `run`, `kill_current`, `stop_move` calls; `stop_move` returns ok unless configured to fail.
- Fake Telegram objects: minimal `Update`-like with `effective_user.id`, `effective_message.text`, `effective_message.reply_text` (an `AsyncMock`), and a `Context`-like with `application.bot_data`.
- `planner_factory.py` — a `factory()` returning a `ScriptedPlanner` configured from env `GO2_TEST_SCRIPT` (JSON list), for CLI subprocess tests via `GO2_TEST_PLANNER`.

### 19.2 Unit tests

**config**: defaults load without a file; explicit missing `--config` → exit 2; unknown key → error naming it; each validation rule in §5.2; relative paths resolve against the config file's folder; `.env` parsing (export, quotes, comments) and real env overriding `.env`.

**process lock**: second acquire in a child process fails with exit 2.

**registry**: loads five skills, names sorted; each invalid case in §10 raises `RegistryError` naming the file (use tmp skill folders with a small importable test module for the entrypoint cases); catalog equals `tests/golden/catalog.txt` and is byte-identical across two loads; registry hash stable for the same inputs and different when the horizon changes.

**tool schema**: property names equal model fields; `required` equals the constants; `maxItems == horizon`; no `strict` key.

**plan validation** (one test per rule, §13.1): lowercase status accepted; `replan_after="2"` rejected (strict); PLAN with 0 steps; DONE with steps; DONE/ABORT without or with blank message; `replan_after` 0, > len, with DONE; too many steps → `horizon_exceeded=True`, `rejection_kind="horizon"`; too many steps **and** a schema error → still `horizon_exceeded=True`; extra top-level key → schema error; missing `params` accepted by the model as `{}`.

**bounds**: unknown skill (message lists names); unknown param; missing required; string for number; bool for number; `2.0` accepted and converted for integer; empty string; enum `" Forward "` normalised to `forward` in filled params; out of range on both sides; defaults filled; several violations collected.

**precheck**: bounds violation in step 3 of 3 → rejection at plan_step 3 with raw params, nothing filled; motion budget crossing at step 2 → rejection with filled params and budget message; steps after `stop_at` are bounds-checked but not budget-checked.

**motion budget**: accumulation; travel and rotation independent; boundary allowed; `copy()` independent.

**policies**: every policy's constants are class attributes; walk/turn formulas; positive timeouts; motion costs.

**render + context** (golden files in `tests/golden/`): first call (all placeholders, exact section order); after checkpoint (`[pending]`, checkpoint notice); after failure (`[abandoned]`, `failure 1 of 3`); after rejection (`-` entry, all steps abandoned, notice uses plan-local step); truncation — 15 entries with K=10 gives exactly `1 + 10` entry lines, the first being `(5 earlier entries omitted)`; `ok` observations only for `context_observations`; 250-char error cut to 200 with `…`; a StepResult with `stderr_tail="SECRET"` never leaks into the context; previous task and posture after a prior task; schema retry equals the original user message + `\n\n` + Rejection section.

**skill response**: dicts from `build_response` validate against `SkillResponse`; error without code → `ValueError`; message collapsed and cut to 300.

**posture**: `derive_posture` thresholds and `None` handling.

**prompts**: every outcome code has an operator message; StopMove warning appended; notices format with their arguments.

**LLM client** — no network: `anthropic.Anthropic(..., http_client=httpx.Client(transport=httpx.MockTransport(handler)))`, and an injected `wait` that records sleeps and returns `False`:

- request body has forced `tool_choice`, two system blocks, one user message, no `strict`;
- valid tool_use parsed; `usage` copied verbatim; `attempts == 1`;
- no tool_use → `no_tool_call`; `stop_reason="max_tokens"` → `max_tokens`;
- 529 then 200 → `attempts == 2`, one `on_infra_retry` call, recorded sleep 1.0;
- 429 with `retry-after: 2` → recorded sleep 2.0;
- 500 three times → `LLMUnavailable`;
- 400 → `LLMUnavailable` immediately, one request;
- connection error (handler raises `httpx.ConnectError`) → retried;
- `wait` returning `True` (stop set) → `LLMInterrupted("operator")`;
- `remaining_s` returning 0 → `LLMInterrupted("task_time_limit")` with no request sent.

### 19.3 Dispatcher loop tests (ScriptedPlanner + FakeExecutor + FakeClock)

1. PLAN(3) → DONE: 3 dispatched steps, `DONE`, `llm_calls == 2`, second request reason `plan_complete`.
2. Immediate DONE with message.
3. ABORT with a question → `ABORTED`, message is the question.
4. Checkpoint: PLAN(3, replan_after=1) → PLAN(...) → DONE; second request shows steps 2–3 `[pending]`, reason `checkpoint`.
5. Failure at step 2 of 3 → step 3 not run; next request `failure`, `[abandoned]`, `failures == 1`.
6. Failure budget: every plan fails, `max_failures=3` → exactly 3 LLM calls, `FAILURE_BUDGET_EXHAUSTED`.
7. Checkpoints never count: 5 checkpoint rounds then DONE with `max_failures=1` → `DONE`.
8. Call budget: always PLAN(1 ok step), `max_llm_calls=4` → `CALL_BUDGET_EXHAUSTED` after exactly 4 calls.
9. Schema retry success: invalid dict then valid plan → continues; both counted as calls; no failure; retry request logged with `retry_of`.
10. Schema retry failure → `LLM_INVALID`.
11. Horizon: plan with h+1 steps → `horizon_rejection` record, retry made, executor never called for that plan.
12. Bounds rejection in step 3 of 3 → nothing runs; failure counted; all three `[abandoned]`.
13. Motion budget: `max_distance_m=2`, walk 1.5 + walk 1.0 → rejected at plan step 2, nothing runs, message mentions travel and 0.5.
14. Budget charged on dispatch even when the step errors.
15. `LLMUnavailable` → `LLM_ERROR`.
16. `LLMInterrupted("operator")` → `STOPPED`, StopMove sent once.
17. Stop during step: executor returns `interrupted/operator` with a stop_move result → `STOPPED`, no further LLM call, the executor's StopMove logged, no second StopMove.
18. Stop between steps (event set by FakeExecutor after step 1) → `STOPPED`, `stop_move` called once by the dispatcher.
19. Stop during an LLM call (`on_call` sets the event; the reply is invalid) → no schema retry, `STOPPED`.
20. `request_stop()` while idle → `"idle"`.
21. Time limit: `FakeClock.advance` past the deadline between steps → `TIME_LIMIT_EXCEEDED`, StopMove sent.
22. Busy: second `run_task` while the first is blocked in FakeExecutor → `BusyError` immediately; planner not called by it.
23. Exception inside the loop → `INTERNAL_ERROR`, `kill_current` and `stop_move` called, lock released, stop event cleared; next task runs normally.
24. Previous task: second task's first request contains the first task's outcome and message.
25. Posture: step with `state_after.posture=sitting` → next request `Posture: sitting`, carried to the next task; killed step without StopMove state → `unknown`.
26. StopMove failure → `stop_move_failed=True`, warning appended.
27. Fault lookup: `stub.faults=[{step: 2, kind: "error"}]` → FakeExecutor receives `fault="error"` only for dispatched step 2.
28. `shutdown(wait_s)` while a step blocks → `request_stop` then (after the wait) `kill_current("shutdown")` and `stop_move("shutdown")`.

### 19.4 Integration tests (`integration` marker; real subprocesses, stub backend)

**Skill contract** (`subprocess.run([sys.executable, "-m", "go2_skills.<name>", params], env=stub env)`), for each of the five skills and both utilities:

- valid params → exactly one stdout line, valid `SkillResponse`, exit 0, `state_before`/`state_after` present with `backend="stub"` (utilities: `state_after` only);
- invalid JSON / missing required key → `status=error`, `code=invalid_params`, exit 1, nothing else on stdout;
- `GO2_BACKEND` unset → `backend_not_configured`;
- `GO2_STUB_NOISE=1` → stdout still exactly one valid line;
- in a **fresh** interpreter subprocess, importing every `go2_skills` module leaves `unitree_sdk2py`, `cv2`, `ultralytics` out of `sys.modules`.

**Stub behaviour**: `sit` then `walk` → walk `error`/`sdk_error`, state file sitting; `stretch` while sitting → error; detect with configured detection → found; unconfigured → `ok`, `object_found=false`; `phone` → `unsupported_object` suggesting `cell phone`.

**Orphan watchdog**: start a skill with a `hang` fault and `GO2_PARENT_PID` set to a PID that is not its parent → it exits within 3 s.

**Executor**:

- fault `error` → `error`; `crash` → `malformed` with exit code 139; `garbage` → `malformed`;
- fault `hang`, timeout 1 s → `timeout`; `os.killpg(result.pid, 0)` raises `ProcessLookupError`; `stop_move` present and ok;
- `kill_current("operator")` from a timer thread during a hang → `interrupted`, `interrupt_cause="operator"`, returns within 2 s, `stop_move` present;
- `remaining_task_s=0.5` with a hang → `interrupted/task_time_limit`;
- child env contains no `ANTHROPIC_API_KEY` (set it in the test env; a test-only skill module under `tests/helpers/` prints its env keys into observations);
- `read_state()` on stub returns a state with the current posture.

**End-to-end** (ScriptedPlanner + real Executor + stub):

- "turn then detect" plan → DONE; the run log has `task_start`, `llm_request`, `llm_response` with `usage`, `plan`, 2× `step_start` + `step_result`, `task_end`; `index.jsonl` has one line; all lines valid JSON with strictly increasing `seq`;
- fault at dispatched step 2 → failure recorded, failure notice in the next request, then DONE;
- stop during a `hang` step via `request_stop` from a timer thread → `STOPPED` within 3 s, `stop_move` record with `ok=true`.

**CLI** (subprocess, `GO2_TEST_PLANNER=planner_factory:factory`, `PYTHONPATH` including `tests/helpers`):

- `catalog` prints the catalog and a 16-hex hash, no API key needed;
- `--reset-stub` writes the initial posture; with `--backend real` → exit 2;
- `run` → exit 0 on DONE, 1 on ABORT;
- `--fault 2:hang --backend real` → exit 2;
- a second concurrent `run` against the same `log.dir` → exit 2 (lock).

**Telegram** (handlers called directly with fake objects):

- unauthorised user → no reply, dispatcher untouched;
- `stop` while idle → `NOTHING_RUNNING`;
- task while busy → `BUSY`, `run_task` not called;
- `stop` text and `/stop` while busy → `request_stop` called, `STOPPING`;
- normal task → `WORKING` then the formatted outcome;
- `build_application(...)` has concurrent updates enabled (`app.update_processor.max_concurrent_updates > 1`) and a `post_stop` callback.

### 19.5 Opt-in tests

**`live_llm`** (`--run-live`, needs `ANTHROPIC_API_KEY`): one task on the stub — "turn left 90 degrees, then tell me if you see a chair" with `stub.detections = {chair = "center:near"}` — ends `DONE`, the message mentions the chair, every plan validated. Print the run log path.

**`robot`** — a supervised manual checklist in `docs/testing.md`, run in this order with the robot standing and the remote e-stop in hand. Record every result in `docs/decisions.md`.

1. `go2-dispatch state` returns within 1 s; record `mode`, `body_height`, `position` standing.
2. `walk` forward 0.5 m; `turn` left 90°; check the angle visually.
3. `stretch` from standing; measure how long the routine takes (tunes `StretchPolicy.SETTLE_S`, OD-10).
4. `detect_object person` with a person in view.
5. Operator `stop` during a 3 m walk; **measure** kill-to-stop latency (`stop_move.timing.stop_call_ms` plus observed time) — record it rather than asserting a number.
6. Step timeout during a walk (temporarily set `WalkPolicy.BASE_S = 0`, `FACTOR = 0.5`) → robot stops; outcome `timeout`.
7. `sit`; measure how long StandDown takes (tunes `SitPolicy.SETTLE_S`); `go2-dispatch state` → record `mode` and `body_height` sitting (resolves OD-2).
8. `walk` while down → record the actual SDK return code and behaviour (resolves OD-4).
9. Stand the robot up with the remote (no `stand` skill; OD-1).

---

## 20. Open decisions

Implement the stated default. Each is designed to be a small change later. Keep this table current in `docs/open-decisions.md`.

| ID | Question | Default implemented in v1 |
|---|---|---|
| OD-1 | No skill can stand the robot up. After `sit`, motion fails until a person stands it up; in `batch` one `sit` affects every later task. Add a `stand` skill (`StandUp()` then `BalanceStand()`, with a settle wait)? | Not added (the agreed skill set is the five skills). The model sees `Posture: sitting` and should ABORT with an explanation. Adding `stand` is one SKILL.md + one module; recommended before experiments. |
| OD-2 | Posture rule for the real robot (`body_height` thresholds 0.15 / 0.22 m, or use `mode`). | Thresholds in `posture.py`; `mode` logged; decide after robot checklist items 1 and 7. |
| OD-3 | Python version supported by `unitree_sdk2py` + `cyclonedds==0.10.2` on the lab machine. | `>=3.10,<3.12`, `tomli` fallback for 3.10. Pin exactly once the lab machine is set up. |
| OD-4 | What `Move()` does while lying down (non-zero code or silent no-op). If silent, `walk` reports `ok` while nothing moved. | Non-zero is an error; record actual behaviour (checklist item 8). |
| OD-5 | `message` on a `PLAN` reply: log only, or also send to the operator as progress? | Logged only. |
| OD-6 | `batch` carries previous task and posture across lines; experiments may need independent tasks. | Carry over. A later `--independent` flag can reset both per line. |
| OD-7 | All (sketch) values: horizon 5, failures 3, calls 20, time limit 300 s, K 10, budget 10 m / 720°, timeout formulas, walk 0.1–3.0 m, turn 5–180°, request timeout 60 s. | As listed, all in config or named constants. |
| OD-8 | Unsupported `detect_object` target is a skill error (counts as a failure, costs a process start) rather than a bounds rejection. | Skill error with suggestions. |
| OD-9 | Exact wording of the system text, notices, operator and transport messages. | Draft wording in §11; must be identical across experimental conditions. |
| OD-10 | Settle waits after `StandDown` (3 s) and `Stretch` (6 s). | Fixed waits via `backend.sleep`; tune from robot checklist items 3 and 7. |
| OD-11 | Bounds and motion-budget rejections count toward `max_failures` (following the professor's definition of failure: skill error, bounds rejection, timeout). If they should not, the call budget still bounds loops. | They count; `rejections` is logged separately in `task_end`. |
| OD-12 | Stop word variants: exact `stop` was agreed; the implementation also accepts any case / surrounding spaces and the Telegram command `/stop`. | Accept these variants. Revert to exact match if unwanted. |
| OD-13 | `max_llm_calls` is the same across horizon conditions; at horizon 1 long tasks may hit it, so `CALL_BUDGET_EXHAUSTED` rates partly reflect the cap. | One config value, logged per task; set it per condition when designing experiments. |
| OD-14 | `uv lock` with the `robot` extra may fail on machines without CycloneDDS. | Try the extra first; fall back to documented manual install (§4.1). |

---

## 21. Documentation (`docs/`)

All documentation is Markdown in `docs/` at the repository root. Root `README.md` is short: one paragraph, a five-command quick start, links into `docs/`. Documentation is part of the definition of done: a change that affects behaviour updates the relevant doc in the same change.

| File | Contents |
|---|---|
| `docs/spec/01-foundations.md` … `04-tests-docs-decisions.md` | The four-part v1 specification, unchanged. |
| `docs/architecture.md` | Condensed overview: purpose, component diagram, the loop, plan contract, context slots, skills and backends, limits and the stop path, run log overview, v2 seams. Links to the spec for detail. |
| `docs/setup.md` | Prerequisites; installing `uv`; `uv sync`; lab machine: CycloneDDS install and `CYCLONEDDS_HOME`, `uv sync --extra robot --extra vision` (or the manual fallback), finding the network interface name (`ip a`), placing `models/yolov8n.pt`; creating `.env` and `config.toml` from the examples; verifying with `go2-dispatch catalog`, `go2-dispatch state` and a stub `run`. |
| `docs/configuration.md` | Every config key: type, default, meaning, (sketch) marker; base dir and path resolution; `.env` format and precedence; CLI overrides; validation rules and exit codes; quoting TOML keys with spaces (`"cell phone" = "left:far"`). Must match `config.py`. |
| `docs/running.md` | CLI commands and exit codes, Ctrl+C behaviour, `batch` file format; Telegram setup (BotFather, token, finding your user id, `allowed_user_ids`), commands and replies; stub vs real; fault injection; switching skill sets with `skills.dir`; the single-instance lock. |
| `docs/skills.md` | SKILL.md frontmatter reference; invocation (`python -m`, params JSON, env vars); response schema with ok and error examples; error codes per skill; policies (timeout formula, motion cost, context observations, class-attribute constants); the no-side-effects-on-import rule; the orphan watchdog; stub rules and fault kinds; step-by-step "add a new skill"; running a skill by hand. |
| `docs/loop-and-context.md` | The loop; return reasons; what counts as a failure and as an LLM call; budgets and outcomes; the exact context layout with a full example request after a failure; truncation; Remaining plan tags; notices; schema retry; the interaction between `planning_horizon` and `max_llm_calls`. |
| `docs/safety.md` | Operator stop, step timeouts, task time limit, the kill + StopMove path and its measured latency, what the robot does between kill and StopMove, orphan handling, motion budget (commanded, not measured), bounds, StopMove failure, single-instance lock; the robot is stationary during LLM calls; the physical/remote e-stop is the final safety measure; supervised-operation rules. |
| `docs/run-log.md` | File naming, envelope, every record type with fields and an example line, `index.jsonl`, computing the study metrics (tokens, latency, calls by return reason, failures, horizon rejection rate, process overhead), a short Python analysis example. |
| `docs/testing.md` | Running tests; markers and flags (`--run-live`, `--run-robot`, `--update-golden`); helpers (ScriptedPlanner, FakeClock, FakeExecutor, fault injection); golden files; the robot checklist from §19.5. |
| `docs/decisions.md` | Decision log: one short entry per decision in the spec (decision, reason, date), including rejected alternatives (OpenClaw runtime, strict tool mode, truncating long plans, dead reckoning, forwarding mid-run messages). Resolved open decisions and robot checklist results are appended here. |
| `docs/open-decisions.md` | The table from §20, kept current. |
| `docs/future-ideas.md` | The list in §23.3, kept current. |

---

## 22. Build order and definition of done

| Milestone | Scope | Done when |
|---|---|---|
| M1 | Repo, `pyproject.toml` (verify `uv lock` on a clean machine), config, process lock, `go2_skills` (policy_base, result, backend, posture, stub, coco, five skills, two utilities) | §19.4 skill contract, stub and watchdog tests pass; each skill runs by hand on the stub. |
| M2 | Models, registry, catalog, policies, bounds, precheck, motion budget | Their unit tests pass; `go2-dispatch catalog` works. |
| M3 | Prompts, render, context, tool schema, plan validation, LLM client | Unit + golden tests and MockTransport tests pass. |
| M4 | Executor | Executor integration tests pass. |
| M5 | Dispatcher, run log | §19.3 loop tests and end-to-end tests pass. |
| M6 | CLI | CLI tests pass; a `--run-live` stub run ends `DONE`. |
| M7 | Telegram | Handler tests pass; a manual phone test works, including busy reply and stop. |
| M8 | Real backend | Robot checklist done under supervision; results in `docs/decisions.md`. Nothing above the backend layer changes for M8. |
| M9 | Docs | Every file in §21 exists and matches the code. |

Definition of done for v1:

- `uv run pytest` passes with no robot, SDK, API key or network.
- The live stub test passes with an API key.
- The robot checklist is done.
- All docs exist.
- No `sys.path` manipulation, no absolute paths, no hardcoded network interface.
- Every (sketch) value lives in config or a named constant.

---

## 23. v2 seams and future ideas

### 23.1 Seams built in v1 (do not remove as dead code)

- `StepResult.verification` (always `"unverified"`).
- `state_before` / `state_after` on every skill response and `state_after` on every StopMove, including the robot's own `position` estimate if published.
- `expect` accepted in SKILL.md frontmatter.
- Status normalisation in one place (`Plan._normalise_status`), so adding `ASK` is local.
- Stub state file and stub `sample_state()`, so v2 can add simulated motion.
- `derive_posture()` as the single posture rule.

### 23.2 v2 (planned; not in scope here)

- Verification: fault detection (fallen, stuck, rejected command) and goal verification per skill, with four verdicts (`verified`, `failed`, `unverified`, `unavailable`); preconditions before dispatch; on `failed`, abort the plan and replan with the failure.
- `ASK` plan status for clarifying questions.
- Stub integrates commanded velocity (prerequisite for developing verification without the robot).

### 23.3 Future ideas

- Forward any message received during a run to the LLM. Decide between letting the model update the plan in place (needs dispatcher support for replacing a running plan) or limiting it to classifying the message as a stop or replying to the sender with text.
- Prompt caching for the static slots. Reduces cost, but cached tokens are reported separately and cache behaviour differs between skill sets, so token comparisons would need uncached-equivalent reporting.
- Geofence (needs a position source; depends on whether the robot's own position estimate is usable).
- Continuous monitoring during a skill (a watchdog that can abort mid-motion).
- Further skill granularity tiers (medium, composite) as separate `skills.dir` folders.
- Horizon enforcement review: if the `horizon_rejection` rate is high (even 1 in 100 is a lot), inspect those plans and either switch to truncation or address it another way (for example, stating the horizon more prominently in the prompt).

---

## Appendix A. Design review: questions and answers

A reviewer read the first draft as the implementer would. Every question is answered below; the answers are already reflected in the sections cited. This appendix is normative.

| # | Question | Answer | Where |
|---|---|---|---|
| 1 | After a kill from another thread, how does the executor know it was a stop rather than a crash? | `kill_current(cause)` records the cause under the executor lock before killing; after exit, a recorded cause decides the outcome (`interrupted`/`timeout`) and StopMove is sent. | §14.3 |
| 2 | Can a stop kill the StopMove process? Does StopMove get its own session? | Never registered as the current process, so it cannot be killed by `kill_current`; started with `start_new_session=True`. | §14.4 |
| 3 | How does the StopMove result reach the dispatcher after an executor-side kill? | `StopMoveResult` is defined; `ExecResult.stop_move` and `StepResult.stop_move` carry it; every one is logged. | §6.3, §14.1 |
| 4 | What do relative config paths resolve against? What is the subprocess cwd? | The config file's folder (else CWD) — the base dir — for both. | §5.3 |
| 5 | Which commands need the API key and reset the stub? | Key only when a real planner is built (`run`, `batch`, `go2-bot`); stub reset only for `run`, `batch`, `go2-bot`, `--reset-stub`. | §16.1, §16.2 |
| 6 | Posture after `sit` is read mid-motion. Wait for the motion to finish? | Yes: fixed settle waits after `StandDown` (3 s) and `Stretch` (6 s), sketch values tuned on the robot. | §8.3, §8.4, OD-10 |
| 7 | What happens to a running skill when the dispatcher exits? | Every exit path calls `shutdown()` (kill + StopMove); skills also watch their parent PID and stop themselves if orphaned. | §7.4, §14.5, §16 |
| 8 | Machine-wide lock? | Yes, `flock` on `{log.dir}/.dispatcher.lock`. | §5.4 |
| 9 | Can stop / time limit interrupt LLM retries? | Yes: checked before each attempt and during backoff (`stop_event.wait`); per-attempt timeout capped at remaining time; `LLMInterrupted`. An in-flight HTTP attempt is not cancelled (the robot is stationary during LLM calls). | §12.3, §12.6 |
| 10 | Do bounds and motion-budget rejections count as failures? | Yes (professor's definition). Recorded as OD-11. | §13.3, OD-11 |
| 11 | Is the schema's `required` intentionally stricter than the model? | Yes; the test compares names and checks `required` against constants. | §12.2 |
| 12 | Hung interpreter shutdown after `emit()`? Which wins, kill or a printed response? | `emit()` uses `os._exit` after `fsync`; a kill cause always wins, the response is kept for the log. | §7.4, §6.3 |
| 13 | Posture after a killed step? | From StopMove's `state_after` (StopMove now samples state), else `unknown`. Real backend reads state at startup. | §8.6, §11.5 |
| 14 | Add `stand` in v1? | Not added (agreed skill set); recommended; OD-1. Checklist reordered so `sit` is last. | OD-1, §19.5 |
| 15 | How does `uv lock` work without CycloneDDS? | Cap Python `<3.12`, `tool.uv.sources` for the SDK, verify in M1, documented fallback. | §4.1, OD-14 |
| 16 | Hatchling finds only one package. | Explicit `packages` list. | §4.1 |
| 17 | Is the run log thread-safe? Who owns the phase? | Writer locks `seq` + write; dispatcher owns `_phase`. | §17.2, §15.1 |
| 18 | LLM latency when retries happen? | `latency_ms` (successful attempt), `total_ms`, `attempts`; `attempt_latency_ms` per retry. | §12.1, §17.3 |
| 19 | Call budget across horizon conditions? | Config value, logged per task and in the index; documented; OD-13. | §17.1, OD-13 |
| 20 | Stop during the first call still triggers a schema retry? | No: interrupts are checked right after every LLM call. | §15.3 |
| 21 | Executor and dispatcher clocks differ. | Executor receives `remaining_task_s`. | §14.1 |
| 22 | PID reuse in `kill_current`? | `poll` and `killpg` under the same lock; `_current` cleared under it. | §14.3 |
| 23 | Secrets in child env? | Stripped. | §14.2 |
| 24 | Which call is faulted? | First action call; never `sample_state` or client construction. | §9.2 |
| 25 | How does the fault reach the process? | Config/CLI → dispatcher looks it up per dispatched step → `GO2_STUB_FAULT` env var for that process. | §14.2, §15.3 |
| 26 | Undefined types and signatures. | Defined: `Clock`, `RunLogFactory`, exceptions, `StopMoveResult`, `AnthropicPlanner`, `Executor`, `PlannerClient.plan`. | §6.6, §6.7, §12.1, §14.1, §17.2 |
| 27 | What does `precheck` return? Params for a rejected step? | `PrecheckResult(rejection, filled)`; rejected bounds steps record raw params. | §13.3 |
| 28 | Schema retry message and notice? | Original user message byte-for-byte + Rejection section. | §11.8, §12.5 |
| 29 | Failure count on every notice after a failure? | Only on failure notices; the Budget section always shows it. | §11.8 |
| 30 | Horizon counting when errors mix? | Horizon checked on raw input first; `horizon_rejection` logged whenever exceeded. | §13.1 |
| 31 | Edited messages / missing user in Telegram? | `filters.UpdateType.MESSAGE`; `effective_message`; ignore missing user. | §16.3 |
| 32 | Telegram shutdown during a task? | `post_stop` → `shutdown(stop_move_timeout_s + 5)`. | §16.3 |
| 33 | Stop latency on the real robot? | `stop_call_ms` recorded; checklist measures instead of asserting. | §8.6, §19.5 |
| 34 | Stop word variants? | Case-insensitive, trimmed, plus `/stop`; OD-12. | §16.3, OD-12 |
| 35 | Catalog example vs rules? | Rules win; the example is corrected and is the golden file. | §10 |
| 36 | `format_outcome` renderer? | Same renderer, including `->`. | §16.1, §11.6 |
| 37 | Truncation test? | Asserts line counts. | §19.2 |
| 38 | Named policy constants? | All policy numbers are class attributes. | §7.2, §8 |
| 39 | Iteration loop vs original time loop? | Iteration loop with `max(1, round(...))`; `duration_s` is commanded; wall time in `timing`. | §8.1 |
| 40 | Does `sample_state` init DDS? Freshness? | Yes; freshness by local arrival time. | §9.1 |
| 41 | NaN in state? | Mapped to `None`. | §9.1 |
| 42 | Posture from `mode`? | `mode` logged; single `derive_posture`; decided on the robot; OD-2. | §9 |
| 43 | Timer and state timing? | `body` returns timing; `run_skill` adds `state_ms`, `total_ms`. | §7.4 |
| 44 | Skill-side param validation? | Minimal checks → `invalid_params`. | §7.3 |
| 45 | Normalised values in filled params? | Yes. | §13.2 |
| 46 | Lax coercion on `Plan`? | `StrictInt`/`StrictStr`; status normalised in a before-validator. | §6.1 |
| 47 | Rejected step in Remaining too? | Yes, all plan steps `[abandoned]`. | §11.7 |
| 48 | Condition label and retry reason in the log? | `run.condition`; `retry_of` on `llm_request`. | §5.2, §17.3 |
| 49 | `usage_totals` with nested fields? | Top-level numeric, non-null fields only. | §17.3 |
| 50 | `.env` format, precedence, validations, exit code? | Specified; real env wins; config errors exit 2. | §5.1, §5.2 |
| 51 | CLI gaps (batch Ctrl+C, reset on real, bot flags, test hook, join)? | All specified. | §16.2, §16.3, §19.1 |
| 52 | Test infrastructure gaps? | `pid` in results, `build_application`, fresh-interpreter import test, `--update-golden`, `on_call` hook; `vision` marker dropped. | §19 |
| 53 | Spec path, reference fix, `state_error` joiner, late-stop race? | `docs/spec/` (four parts); joiner `"; "`; `request_stop` returns `idle` once the task is `ending`. | Each part's header, §6.2, §15.1 |
| 54 | Over-engineering candidates? | Dropped: fallback parsing of earlier stdout lines, `vision` marker. Kept (cheap, test real risks): `garbage` fault, noise test, `during`, `git_commit`, 4000-char trimming (Telegram limit). | §14.3, §19 |
