# Step 7 of 7: the run log as typed events, and a simpler task ending

Read `refactor-00-context.md` first (same branch as this file: `git show origin/refactor_prompts:refactor-00-context.md`). It holds the background, the target layout, the invariants and the working rules for this whole series. Then read root `CLAUDE.md`, `docs/README.md`, `dispatcher/CLAUDE.md` and `dispatcher/docs/run-log.md`.

## Goal

1. **Replace the 15-method record protocol with typed events and one `emit(event)`.** A new record type becomes one event dataclass plus the handlers that care about it, not an edit to five classes and a Protocol.
2. **`dispatcher/runlog/`** as a sub-package: events, the JSONL writer, observers.
3. **Simplify how a task ends** in the loop, now that every notification goes through one call.

The run log is the study dataset. The files on disk must not change: same file names, envelope, record types, field names, field order where the writer fixes one, and `index.jsonl`. Console and Telegram output stay the same too.

## Starting point

Steps 1 to 6 are done; `dispatcher/` has `planning/`, `llm/`, `execution/`, `transports/`, `app.py`. Find these by symbol:

- `dispatcher/runlog.py`: `RunLog` (the JSONL writer), the `TaskRecords` protocol (one method per record type: `task_start`, `llm_request`, `llm_retry`, `llm_response`, `llm_error`, `llm_interrupted`, `plan`, `plan_invalid`, `step_start`, `step_result`, `precondition_failed`, `stop_requested`, `stop_move`, `exception`, `task_end`), `TaskObserver` (adds `robot_sample` and `robot_link` from the state stream), `TaskLog` (adds `path` and `close`), `NullLog`, `TeeLog`, `notify`, `RunLogFactory`, `SessionInfo`, `stream_path`, and file-format constants that the report in `analysis/` imports.
- The same protocol is implemented again by `ConsoleTrace` (`dispatcher/transports/console_trace.py`) and `TelegramProgress` (`dispatcher/transports/telegram_progress.py`). Each handles a few records and has empty methods for the rest.
- `llm_retry` takes a bare `dict`.
- In `dispatcher/dispatcher.py`: `Dispatcher` builds its own `RunLogFactory`; `_Ending` is unpacked field by field into `_end()`; the phase and stop state handling is interleaved with the loop.

Notes from step 6 (see "Step status" in the context file for detail):

- `analysis/` is owned by `runs-analyzer`, not `dispatcher-dev`. If the format constants move into `dispatcher/runlog/`, `analysis/logs.py` and `analysis/report.py` change import paths: that is a `runs-analyzer` task after the dispatcher side. "The report reads both logs with the same result" is also checked by running `uv run go2 report <folder>` on the reference and the new log folder.
- `StepResult` serialises through a wrap `model_serializer` that flattens `process: ProcessRun` back to the flat `step_result` keys; `dispatcher/tests/unit/test_step_result_dump.py` pins the order. The writer's `step_result` serialiser must go through it.
- `app.build_dispatcher` (in `dispatcher/app.py`) is where to inject the `RunLogFactory`.
- Import contracts to touch with `tests-dev`: "Only the composition root, execution and the registry import the services" lists `dispatcher.runlog` among its sources (a package keeps the name); "The analysis package reads logs only" has `allow_indirect_imports = true` only because `runlog` imports `context` and `llm` under `TYPE_CHECKING`: remove those imports and make it indirect if you can.
- Emitters in `execution/`: the stop path (`execution/stop_path.py`) and executor (`execution/executor.py`) call the observer; `state_stream.py` emits `robot_sample` / `robot_link`.

## Design guidance

- **One frozen dataclass per record type**, carrying the typed objects the methods take today (`Plan`, `StepResult`, `LLMResult`, …), not pre-serialised dicts. Serialisation to the on-disk record stays in the writer, in one place per event, so the event types can evolve without touching the file format. `llm_retry` gets a typed event like the rest.
- **One sink protocol**: `emit(event)`. The writer handles every event (make an unhandled event type a loud error in the writer, so a new event cannot be silently dropped from the dataset). Observers handle the events they care about and ignore the rest by default: no empty methods. Dispatch by event type with whatever is simplest and type-checks (a `match` statement, `functools.singledispatchmethod`, or a small type-to-handler table); pick one and use it everywhere.
- **`TeeLog` and `NullLog` shrink to a few lines** or disappear (a list of sinks; an empty list).
- **The state-stream records** (`robot_sample`, `robot_link`) are high-rate and go to a separate stream file. Decide whether they are events on the same `emit` or keep their own narrow interface; keep the hot path cheap and the stream file identical either way.
- **Threading**: records are emitted from the loop thread, the stop path and the state stream. Keep whatever locking and ordering guarantees the writer has today; say in the proposal what they are.
- **`RunLogFactory` is injected** by `app.py`; the dispatcher receives it (or a sink factory) and does not construct it. Tests then pass a fake without patching.
- **Task ending**: with one `emit`, look at `_Ending`, `_end()` and the phase/stop state handling, and make the ending one object passed whole, with the loop reading top to bottom. This part is the lowest priority of the whole series. The loop is safety-relevant (stop handling) and reads well today, so the bar is: clearly simpler and behaviour-identical, or leave it and say why. Do it as its own commit so it can be dropped.
- **File-format constants** (file names, suffixes, time format, token keys) live with the writer; `analysis/` imports them from there and nothing else from the dispatcher.

## Scope

In: `dispatcher/runlog/` (events, writer, sinks, factory, session info); `ConsoleTrace` and `TelegramProgress` on the new interface; every emitter in the loop, `execution/` and the state stream; the factory injection; the task-ending simplification; tests (`test_console_trace.py`, `test_telegram_progress.py`, the dispatcher and state-stream tests, test fakes and helpers that implement the old protocol); `dispatcher/docs/run-log.md` (how records are produced, if it names the protocol; the record reference itself must not need changes) and `loop-and-context.md` if it describes the ending; `dispatcher/CLAUDE.md`.

Out: any change to a record's content, the envelope, file layout, `index.jsonl`, console or Telegram text; new record types; an async or queued log pipeline; a plug-in system for observers.

## Decisions taken (user, before implementation)

A first session ran task 1 and task 2 (the read-only `dispatcher-dev` proposal) and the user approved every recommendation below. Do not re-ask them; brief them as decided. Re-check names against the code (the repo wins), but the shape is settled.

**Corrections to "Starting point"**: `execution/stop_path.py` and `execution/executor.py` emit nothing. `Dispatcher` writes every `stop_move` record; the only emitter in `execution/` is `state_stream.py`.

1. **Events and dispatch (approved)**: one `@dataclass(frozen=True)` per record in `dispatcher/runlog/events.py`, each writing the record of the same name:
   - `TaskStart(task, source, sender_id, previous: TaskSummary|None, posture, start_state: StartState)`; `LlmRequest(call_index, return_reason, retry_of, user_text)`; `LlmRetry(retry: InfraRetry)`; `LlmResponse(call_index, result: LLMResult)`; `LlmError(call_index, detail)`; `LlmInterrupted(call_index, cause)`; `PlanRecord(call_index, plan: Plan, stop_at)` (not `Plan`, to avoid the model clash); `PlanInvalid(call_index, tool_input, check: PlanCheck, horizon)`; `StepStart(ref, dispatch)`; `StepRecord(result: StepResult, budget: MotionBudget, failures, posture)` (the writer keeps calling `result.model_dump(mode="json")`, so the wrap serializer stays the one place that flattens `process`); `PreconditionFailed(failure)`; `StopRequested(source, during)`; `StopMove(result: StopMoveResult)`; `ExceptionRaised(where, error)`; `TaskEnd(result: TaskOutcome, rejections, horizon_rejections, usage_totals, budget)`; `RobotSample(state, link, rx_mono)` and `RobotLink(link, rx_mono)` (stream thread; no task record).
   - `InfraRetry` is a frozen dataclass in `dispatcher/llm/client.py` (`call_index, attempt, error_type, status_code, attempt_latency_ms, sleep_s`) built by `llm/retry.py`; the loop wraps it in `LlmRetry` (planning/llm may not import runlog). Nothing else in planning or llm changes.
   - `TaskEvent` = union of the 15 task events; `Event = TaskEvent | RobotSample | RobotLink`. `TaskSink(Protocol).emit(event: TaskEvent)`, `Observer(Protocol).emit(event: Event)`.
   - **Dispatch**: `match` with class patterns everywhere. The writer ends with `case _: assert_never(event)` (`typing_extensions`, Python 3.10) so basedpyright flags a missing case and an unknown type raises; observers end with `case _: pass`. Rejected: `singledispatchmethod` (no exhaustiveness check, weak typing), a type-to-handler table (loses field narrowing).
   - **Layout**: `dispatcher/runlog/__init__.py` docstring only (no re-exports); `format.py` (`INDEX_FILE`, `STREAM_SUFFIX`, `FILE_TIME_FORMAT`, `TOKEN_KEYS`, `ROBOT_IO`, `ENVELOPE_KEYS`, `RUN_ID_PREFIX_LEN`, `stream_path`; imports only `dispatcher.config`); `events.py` (events, unions, the two protocols); `session.py` (`SessionInfo`, version and git helpers); `writer.py` (`RunLog`, `RunLogFactory` with `open`, `append_index`); `sinks.py` (`publish(event, log: RunLog|None, observers)`, which replaces `TeeLog`, `NullLog` and `notify`).
2. **State-stream records (approved: same `emit`)**: `StateStream` takes `observers: Sequence[Observer]` and calls `emit(RobotSample(...))` / `emit(RobotLink(...))` through its existing warn-once guard. The stream file keeps its own inline write, so it stays byte-identical. Cost: one small dataclass per sample at 10 Hz, as today's per-sample lambda. Rejected: a separate narrow interface (keeps a second observer protocol; `ConsoleTrace` today carries two empty methods for it). Only Telegram handles these (the live robot line, `robot_line()` in `telegram_progress.py`); console ignores them.
3. **Task ending (approved: reduced form, its own droppable commit)**: `_end(t, ending: _Ending)` takes the object whole; `run_task` returns `self._end(t, ended.ending)`; `_internal_error` builds `_Ending("INTERNAL_ERROR", stop_move=smr, exception_type=...)`. A `_phase(name)` context manager that sets `between` on exit replaces the three `_set_phase("between")` calls in `_call` and the try/finally in `_run_step`; `LlmRequest` is still emitted inside it so `during` is unchanged. `_stop`, `_state_unavailable`, `_service_lost` stay separate (merging needs extra parameters; does not clear the bar).
4. **Factory injection (approved)**: `Dispatcher(cfg, registry, planner, executor, *, runlog: RunLogFactory, clock, observers: Sequence[Observer] = ())`. `app.build_dispatcher` builds `RunLogFactory(cfg.run_log_dir, uuid4().hex, SessionInfo.collect(cfg, planner.surface))` from the same planner it passes in. This rewrites the recorded decision in `dispatcher/docs/run-log.md` that the log is built from the planner's surface "by construction" inside `Dispatcher`: the composition root now guarantees it; record the reason and the rejected alternative (inject a `surface -> RunLogFactory` callable). Tests: a `make_runlog(cfg, planner)` helper in `dispatcher/tests/helpers/fakes.py` for the 11 `Dispatcher(...)` call sites (4 unit test files, 3 integration files); the two `monkeypatch.setattr(RunLog/RunLogFactory, ...)` tests pass a failing factory instead; `Recorder`/`Broken` in `test_console_trace.py` become `emit` recorders; `FakeStream` takes `observers`.

**Threading (keep exactly)**: `RunLog._lock` covers envelope, `seq`, write and flush; `_index_lock` covers `index.jsonl`. The loop thread writes every record except `stop_requested` and the shutdown `stop_move`, which the transport thread writes to the plain log under `_state_lock`, and only after `kill_current` and StopMove, outside the lock, tells the observers. So `Dispatcher._log: RunLog|None` stays the plain writer: `request_stop` does `self._log.emit(ev)` under the lock, then `publish(ev, None, observers)` after the kill. `_Task` holds `log` and `observers`; `t.emit(ev)` calls `publish(ev, t.log, t.observers)`. Writer errors still propagate (the `task_end` try/except stays); observer errors are still swallowed. The stream thread writes only its own file.

**Adding a record type afterwards** (for the "Done when" report): the class and its `TaskEvent` member in `events.py`; one `case` in `writer.py` (basedpyright fails until added); the emit call; observer cases only if wanted; the record table in `run-log.md`; a field-order test.

**Commit split proposed by dispatcher-dev, each green**:
1. `InfraRetry` typed; `RunLog.llm_retry` writes its fields explicitly in today's order.
2. Move to `runlog/` (old method API, no shim), with the `runs-analyzer` import update in the same commit (`analysis/logs.py`, `analysis/report.py` if it imports runlog, `analysis/tests/unit/test_report.py`, `dispatcher/tests/unit/test_report.py` → `dispatcher.runlog.format`) and `tests/integration/test_end_to_end.py` (imports `INDEX_FILE`, `stream_path`). Run dispatcher-dev first, then runs-analyzer, review and commit together.
3. Events, `emit`, `match` in the writer, `publish`, both observers, `StateStream`, docs: one atomic commit (no parallel old/new interface).
4. Factory injection plus the `run-log.md` decision rewrite.
5. The ending.

**tests-dev follow-up**: "Only the composition root, execution and the registry import the services" lists `dispatcher.runlog`, which still covers the package: no change. Drop `allow_indirect_imports` from "The analysis package reads logs only" (`format` reaches only config, models, prompts and contract) if `lint-imports` passes, and update the contract comment and `tests/docs/testing.md`.

### Reference log (task 1 shortcut)

Baseline at the end of step 6, measured in the proposal session: 615 passed, 1 skipped; 97 integration; 21 import contracts kept; ruff clean; registry hash `768d05c6633b8fdc`. The scratchpad does not survive sessions, so recapture before any code change (about 6 s):

```bash
rm -rf /tmp/rf && uv run pytest -m integration -q -p no:randomly tests/integration/test_end_to_end.py --basetemp=/tmp/rf
S=<scratchpad>; mkdir -p $S/ref; for d in /tmp/rf/*/; do [ -d $d/logs ] && cp -r $d/logs $S/ref/$(basename $d); done
```

- Use a short `--basetemp` under `/tmp`: under the scratchpad path the robot service fails with `OSError: AF_UNIX path too long` and every test fails.
- The 12 end-to-end tests (scripted planner, stub) produce `task_start`, `llm_request`, `llm_response`, `plan`, `step_start`, `step_result`, `stop_move`, `stop_requested`, `precondition_failed`, `task_end`, plus `stream_start`, `sample`, `stream_error` in the stream file. Not produced: `llm_retry`, `llm_error`, `llm_interrupted`, `plan_invalid`, `exception`; those rely on the unit tests (`test_runlog*`, field-order tests) for byte equality.
- After the change, rerun into `/tmp/rf2`, copy to `$S/new` the same way, and run `python3 $S/cmp_logs.py $S/ref $S/new`; it must print `differences 0`. Two baseline runs compared with it gave 0 differences across 36 files. It ignores timestamps, monotonic times, ids, pids, durations, floats, the temp path and the stream-file sample counts (stream files compare by record type and key order only), and compares top-level `seq`, record order, key order and all other values.

`cmp_logs.py` (write it to the scratchpad):

```python
"""Compare two captured log trees: files, record types/order, key order, values except volatile."""
import json, sys, re
from pathlib import Path
VOL = {"ts","t_mono_ms","session_id","run_id","duration_ms","pid","t","age_ms","seq_state","started_at","ended_at","elapsed_s","wall_s","latency_ms"}
def norm(v, k=None, d=0):
    if k in VOL or (d > 1 and k == "seq"): return "<v>"
    if isinstance(v, dict): return {kk: norm(vv, kk, d + 1) for kk, vv in v.items()}
    if isinstance(v, list): return [norm(x, None, d + 1) for x in v]
    if isinstance(v, float): return "<f>"
    if isinstance(v, str): v = re.sub(r"/tmp/rf\d*/", "<tmp>/", v); v = re.sub(r"\d{4}-\d\d-\d\dT[\d:.]+(\+00:00)?", "<iso>", v); return re.sub(r"[0-9a-f]{8,}", "<id>", re.sub(r"\d{8}T\d{6}", "<time>", v))
    return v
def load(root):
    out = {}
    for p in sorted(Path(root).rglob("*.jsonl")):
        rel = re.sub(r"\d{8}T\d{6}_[0-9a-f]+", "<file>", str(p.relative_to(root)))
        if rel.split("/")[0].endswith("current"): continue
        recs = [json.loads(l) for l in p.read_text().splitlines()]
        if "state_stream" in rel:  # sample count depends on timing: compare key sets and first record only
            recs = [(r.get("type"), list(r.keys())) for r in recs]
            recs = sorted(set((t, tuple(k)) for t, k in recs))
        else:
            recs = [(list(r.keys()), norm(r)) for r in recs]
        out.setdefault(rel, []).append(recs)
    return out
a, b = load(sys.argv[1]), load(sys.argv[2])
bad = 0
for k in sorted(set(a) | set(b)):
    if k not in a or k not in b: print("FILE only in one:", k); bad += 1; continue
    for x, y in zip(sorted(map(json.dumps, a[k])), sorted(map(json.dumps, b[k]))):
        if x != y:
            bad += 1; xs, ys = json.loads(x), json.loads(y)
            print("DIFF", k)
            for i, (rx, ry) in enumerate(zip(xs, ys)):
                if rx != ry:
                    d = [kk for kk in set(rx[1]) | set(rx[1]) if not isinstance(rx[1], list) and rx[1].get(kk) != ry[1].get(kk)] if isinstance(rx, list) and len(rx) == 2 and isinstance(rx[1], dict) else rx
                    print("  rec", i, "keys differing:", d, "\n   ref:", json.dumps(rx)[:400], "\n   new:", json.dumps(ry)[:400]); break
            if len(xs) != len(ys): print("  lengths", len(xs), len(ys))
print("files", len(a), "differences", bad)
```

## Tasks, in order

1. **You**: check steps 1 to 6; baseline (default and integration suites, `lint-imports`, registry hash). **Capture the reference log** as in "Reference log" above, before any code change.
2. **Done**: the proposal and the user's decisions are under "Decisions taken" above. Go straight to task 3, briefing the agents with those decisions.
3. **`dispatcher-dev`** (then `runs-analyzer` for the import paths, then `tests-dev` for the contract), following the commit split above: events, writer and sinks, with the old protocol removed in the same commit (no parallel old and new interface left behind); observers and all emitters converted; factory injected from `app.py`. If it must be split to stay reviewable: first the events and writer behind the existing method names inside the writer only, then the emitters and observers, each commit green.
4. **You**: produce the same scenario's log again and compare with the reference: same files, same record types in the same order, same key sets per record, same values apart from timestamps, durations, ids and pids. Any other difference is a defect; send it back.
5. **`dispatcher-dev`**: the task-ending simplification, as approved. Separate commit.
6. **`tests-dev`**: only if shared helpers, fakes or import contracts need it (for example `runlog` may not import the loop, execution or transports; `analysis` imports only the format constants).
7. **You**: `docs/architecture.md` (components and repository layout now match the target layout in full; check the whole "Design decisions" section still describes the system as built) and `docs/README.md`.

## Done when

- One `emit` protocol; no class implements one method per record type; `ConsoleTrace` and `TelegramProgress` contain handlers only for the events they present.
- Adding a record type is demonstrably one event class, one writer serialiser and optional handlers: the agent's report names the exact places.
- `Dispatcher` receives its log factory; `_Ending` is passed whole or gone.
- The before and after logs match as described in task 4; `uv run pytest -q` and `uv run pytest -m integration` pass; the report (`analysis/`) reads both logs with the same result; the registry hash and golden files are unchanged.

## Closing the series

This is the last step. In addition to the end-of-step report:

- Compare the repository against the target layout in the context file and list every difference, each as either "intended, recorded in `docs/architecture.md`" or "open, added to `docs/roadmap.md` with an owner" (delegate the roadmap edit to its owner).
- Confirm with one command each that the faults list is closed: the import contracts (`uv run lint-imports`), no backend branches outside the loader, no skill names in the dispatcher, no fault code in `Motion`.
- Remind the user that the `refactor_prompts` branch holds only working briefs and can now be deleted.
