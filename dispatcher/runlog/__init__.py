"""JSONL run log: one file per task plus an index (dispatcher/docs/run-log.md).

``format`` holds the file-format constants (the only module ``analysis`` imports), ``session`` the per-process
``task_start`` fields, ``writer`` the log file and index writer, ``sinks`` the record protocols and their wrappers.
"""
