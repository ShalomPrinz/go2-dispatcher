# Run-log report

`go2 report` reads one folder of run logs and prints the study's latency numbers. It reads logs only: no API key, lock, robot or network. The log format, the records it reads and the definitions of the study metrics are in [run-log.md](../../dispatcher/docs/run-log.md); this page says what the report selects, computes and prints.

## The package

`analysis` is a top-level package that only reads run logs. From the project it imports only the run log's file-format constants (`dispatcher.runlog`: `INDEX_FILE`, `STREAM_SUFFIX`, `FILE_TIME_FORMAT`, `TOKEN_KEYS`) and `dispatcher.config.Provider`; only the CLI (`dispatcher.transports.cli`) imports it. The import contracts ([testing.md](../../tests/docs/testing.md#import-boundaries)) enforce that only the CLI imports `analysis` and that `analysis` imports none of `dispatcher.registry`, `execution`, `app`, `dispatcher.dispatcher`, `planning`, `llm`, `context`, `transports`, `skills` or `services`; they do not forbid `dispatcher.models`, `prompts`, `operator_messages` or `process_lock`, so staying with `runlog` and `config` is a rule of review. The contracts hold for `analysis/tests/` too, so the report's own cases live there and the `go2 report` command cases (arguments, exit codes, default folder) live with the CLI's tests in `dispatcher/tests/unit/test_report.py`.

| Module | Holds |
|---|---|
| `analysis/logs.py` | Reading a folder: the `index.jsonl` rows (`read_index`), the task files the index does not list (`task_files`, `file_start`), one task log parsed into a `Task` with its `Call`s (`read_task`), the skip reasons (`Skip`, `SKIP_*`), `NoLogs`, and `SkillSets`, the motion and stationary skill names a task is measured with. |
| `analysis/report.py` | The report: `build` (selection, grouping, statistics), `render` (the printed text), `parse_bound` (a `--since` / `--until` value), `Report` and its groups, `STUDY_FOLDER`, `TARGET_CALL_MS`, and the default skill lists `MOTION_SKILLS` and `STATIONARY_SKILLS`. |

The CLI only parses the arguments, calls `build` and prints `render`; `NoLogs` exits 1 with its message on stderr.

## Usage

```
go2 report [FOLDER] [--since WHEN] [--until WHEN] [--model NAME]
go2 report logs/openrouter/real --since 2026-10-29T14:57 --until 2026-10-29T17:00
go2 report --model claude-sonnet-5-5
```

- `FOLDER`: default `{log.dir}/anthropic/real` (`STUDY_FOLDER` under `log.dir`), the study folder. An explicit folder does not read the config, so a report never depends on the current config.
- `--since` / `--until`: `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM`, local time as recorded at task start, both ends included. A bare `--until` date covers that whole day. A value with a UTC offset, or one that cannot be read, is a usage error.
- `--model`: only tasks whose `index.jsonl` `model` is exactly `NAME`. No matching task exits 1 with a message.

It prints a table to stdout, in seconds, one section per model, registry hash and condition: per call type, per task, and the time split. Exit codes are in [running.md](../../docs/running.md#exit-codes).

## What it computes

The report selects tasks through `index.jsonl` by `ts_start`, read as the local time written in the row, and with `--model` by the row's `model`, an exact string match. The model is fixed per task (`llm.model`), so a selected row's whole log belongs to that model. One group per `model` + `registry_hash` + `condition`, never pooled.

The per-call target is 4 s (`TARGET_CALL_MS`), the advisor's. **The primary reading is the median `total_ms` of the `all calls` row, with p90 beside it**: `total_ms` is what the operator waits through, retries and backoff included.

| Number | Definition |
|---|---|
| Call type | the `llm_request.return_reason` of the response's `call_index` |
| `first call` / `later calls` | `initial` / every other type, a `schema_retry` of the first call included. These two are the only rows comparable to OpenClaw |
| Median, p90, max | of `llm_response.total_ms`; p90 interpolates linearly between ranks (the numpy default), and a single value is its own p90 |
| Latency | median `llm_response.latency_ms` (successful attempt only) |
| `<4s` | share of calls with `total_ms` below 4000 |
| Tokens | median per call of the `usage` keys in `runlog.TOKEN_KEYS` for the row's provider |
| Calls without a response | requests that ended in `llm_error` or `llm_interrupted`: counted on their own line, in no row, because they have no `total_ms`. Their time falls under `other` |
| Task duration | `task_end.duration_ms` |
| Motion skill | a skill that physically moves the robot, by name ([below](#skill-lists)), not by budget cost: `sit` and `stretch` have zero `motion_cost` and still move it. `detect_object` is stationary. A skill in neither list counts as non-motion and the report names it in a warning |
| First motion | `t_mono_ms` of the task's first `step_start` of a motion skill. Tasks with no motion are left out and counted |
| Model wait | sum of `total_ms` over the task's responses |
| Calls, replans, infra retries per task | `llm_response` records; `llm_request` with `plan_complete`, `checkpoint` or `failure`; `llm_retry` records |
| Outcomes | every selected task in the group, measured or not |
| Time split | over the group's measured tasks: model wait; `step_result.duration_ms` of motion skills and of the other dispatched skills (a rejected step has `dispatch = null` and ran nothing); `other` is the rest of the summed task durations (the task-start state read, the vision ready wait before a step of a skill that declares `uses_vision`, stops the dispatcher sends itself, calls without a response). A killed step's stop runs inside its `step_result.duration_ms`, so it counts under that step's skill row. A negative remainder shows `other` as 0 and prints a warning with the excess |

A task is measured only when its log has `task_start`, `task_end`, at least one `llm_response` and every field above. Otherwise it is skipped and counted by reason in its group: `log file missing or unreadable`, `no task_end`, `no model response` (`STATE_UNAVAILABLE`, or an `LLM_ERROR` on the first call), `older log, fields missing`. A log file with no index row (the process died mid-task) and an index line that cannot be read belong to no group and are counted at the top of the report, also under `--model`, because neither can be tied to a model; an older row with no `model` is such a line. No index rows and no log files in the folder, or none in the date window or for the selected model, exits 1 with a message.

## Skill lists

`MOTION_SKILLS` (`walk`, `turn`, `sit`, `stretch`) and `STATIONARY_SKILLS` (`detect_object`) are the default lists for the built-in skills, matched against `ref.skill`, which every log with `ref` holds. They are a documented default, not read from the registry at report time ([below](#design-decisions)). A test, `test_skill_sets_match_the_catalog`, checks them against each built-in skill's `moves` declaration in its `SKILL.md`, which the registry checks against the skill's `POLICY` ([contract.md](../../contract/docs/contract.md)), so a built-in skill added without being classified fails the suite. A study with another skill set extends the lists.

## Design decisions

- **`analysis` is a separate top-level package because it only reads logs.** Its import contract allows the run log's file-format constants and `Provider` and nothing of the run time, so the report cannot reach the loop, the executor, the registry or a provider, and reading old logs never depends on the code that runs tasks. The CLI stays its one entry point (`go2 report`), so it reads the config's `log.dir` like every other command. Rejected: keeping it in `dispatcher/`, where nothing stops it reaching the loop and execution; a stand-alone script, which would not share `TOKEN_KEYS` or get tested with the project.
- **Motion is classified by skill name, not by `motion_cost`.** The budget cost answers how much motion is charged; `sit` and `stretch` are charged nothing but move the robot, and time to first motion is a headline number ([project.md](../../docs/project.md)). An unknown skill counts as non-motion, the choice that cannot flatter time to first motion, and the report names it. Rejected: reading `moves` from the registry at report time, which reads today's catalog rather than the logged run and cannot classify a removed skill; deriving it from the module at report time, which ties reading old logs to the current catalog; logging a new field, which old logs lack.
- **Whole tasks only, with `total_ms` as primary.** A task with partial records is skipped and counted, so the per-task numbers and the time split all describe the same tasks and a skipped task stays visible. A zero-call task would otherwise pull down task duration and calls per task, measuring provider or link trouble instead of planning latency. `total_ms` is primary because the operator waits through retries; a provider having a bad day is part of the wait. Rejected: `latency_ms` as primary, which hides retries and backoff; a mean, which one 60 s call dominates; nearest-rank p90, which jumps between observed values at the small sample sizes here and matches no default a later baseline script would use; pooling groups, because different registry hashes saw different prompts; counting partial tasks in the distributions, which mixes tasks that have no call data into per-call-comparable numbers.
- **Reading and reporting are separate modules.** `logs.py` turns files into `Task`s and skip reasons; `report.py` groups and prints them and passes its skill lists to `read_task` as a `SkillSets`, so the parser holds no study default.
