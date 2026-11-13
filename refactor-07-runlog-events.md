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

## Decisions to confirm with the user before delegating

Have `dispatcher-dev` produce, read-only: the event list (one line each, with the fields), the sink protocol, the dispatch mechanism, the treatment of state-stream records, the threading guarantees, and a sketch of the simplified ending. Then ask in one question:

1. The event set and the dispatch mechanism.
2. State-stream records: same `emit` or a separate narrow interface.
3. Whether to do the task-ending simplification as proposed, in a reduced form, or not at all.

## Tasks, in order

1. **You**: check steps 1 to 6; baseline (default and integration suites, `lint-imports`, registry hash). **Capture a reference log**: run one integration scenario on the stub that produces every record type it can (success, a failed step with replanning, a stop, a fault), and keep the log folder in your scratchpad for comparison. `tests-dev` or `dispatcher-dev` can tell you which existing integration test is closest; use a test planner, not the paid API.
2. **`dispatcher-dev`** (read-only): the proposal. Ask the user.
3. **`dispatcher-dev`**: events, writer and sinks, with the old protocol removed in the same commit (no parallel old and new interface left behind); observers and all emitters converted; factory injected from `app.py`. If it must be split to stay reviewable: first the events and writer behind the existing method names inside the writer only, then the emitters and observers, each commit green.
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
