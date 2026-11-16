"""The process's saved stdout fd (contract/docs/contract.md)."""

from __future__ import annotations

import os
import sys

from contract import process


def test_capture_stdout_keeps_junk_off_the_response_line(tmp_path, monkeypatch):
    """Junk via print() and os.write(1) after capture_stdout() goes to stderr; stdout holds only the line."""
    out_path, err_path = tmp_path / "stdout", tmp_path / "stderr"
    line = '{"skill":"walk","status":"ok"}\n'
    saved = os.dup(1), os.dup(2)
    monkeypatch.setattr(process, "_saved_stdout_fd", None)
    with open(out_path, "wb") as out, open(err_path, "wb") as err:
        os.dup2(out.fileno(), 1)
        os.dup2(err.fileno(), 2)
        try:
            process.capture_stdout()
            with open(1, "w", closefd=False) as fd1:
                monkeypatch.setattr(sys, "stdout", fd1)
                print("junk via print()", flush=True)
            os.write(1, b"junk via os.write(1)\n")
            process.write_raw_stdout(line)
        finally:
            assert process._saved_stdout_fd is not None
            os.close(process._saved_stdout_fd)
            os.dup2(saved[0], 1)
            os.dup2(saved[1], 2)
            os.close(saved[0])
            os.close(saved[1])
    assert out_path.read_text() == line
    assert err_path.read_text() == "junk via print()\njunk via os.write(1)\n"
