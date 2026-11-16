"""JSONL run log: one file per task plus an index (dispatcher/docs/run-log.md).

``format`` holds the file-format constants (the only module ``analysis`` imports), ``session`` the per-process
``task_start`` fields, ``events`` one frozen dataclass per record (plus the two state-stream events), ``writer`` the
log file (``RunLog.emit``) and index writer, ``sinks`` the sink and observer protocols and ``publish``.
"""
