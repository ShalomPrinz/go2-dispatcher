"""Config unit tests (§19.2 config)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from go2_dispatcher.config import (
    Config,
    build_config,
    load_config,
    load_config_and_env,
    load_env_file,
    parse_env_text,
)
from go2_dispatcher.models import ConfigError
from helpers import REPO_ROOT, make_config


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


# --- loading ------------------------------------------------------------------


def test_defaults_load_without_file(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    cfg = load_config(None)
    assert isinstance(cfg, Config)
    assert cfg.base_dir == tmp_path.resolve()
    assert cfg.loop.planning_horizon == 5
    assert cfg.robot.backend == "stub"
    assert cfg.log.dir == tmp_path.resolve() / "runs"
    assert cfg.log.dir.is_dir()
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "config.toml" in err


def test_explicit_missing_config_is_error(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.toml")


def test_explicit_missing_config_exits_2(tmp_path):
    code = (
        "from go2_dispatcher.config import load_config_and_env; "
        f"load_config_and_env({str(tmp_path / 'nope.toml')!r})"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          cwd=tmp_path)
    assert proc.returncode == 2
    assert proc.stderr.startswith("Config error: ")


def test_unknown_key_named(tmp_path):
    p = write(tmp_path / "c.toml", "[loop]\nplanning_horizon = 3\nbogus_key = 1\n")
    with pytest.raises(ConfigError, match="loop.bogus_key"):
        load_config(p)


def test_unknown_section_named(tmp_path):
    p = write(tmp_path / "c.toml", "[nonsense]\na = 1\n")
    with pytest.raises(ConfigError, match="nonsense"):
        load_config(p)


def test_invalid_toml(tmp_path):
    p = write(tmp_path / "c.toml", "[loop\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_relative_paths_resolve_against_config_folder(tmp_path, monkeypatch):
    sub = tmp_path / "conf"
    sub.mkdir()
    abs_weights = tmp_path / "w.pt"
    p = write(sub / "config.toml",
              f'[skills]\ndir = "myskills"\n[log]\ndir = "logs"\n'
              f'[stub]\nstate_file = "st/state.json"\n'
              f'[robot]\nyolo_weights = "{abs_weights}"\n')
    monkeypatch.chdir(tmp_path)
    cfg = load_config(Path("conf/config.toml"))
    base = sub.resolve()
    assert cfg.base_dir == base
    assert cfg.skills.dir == base / "myskills"
    assert cfg.log.dir == base / "logs"
    assert cfg.stub.state_file == base / "st/state.json"
    assert cfg.robot.yolo_weights == abs_weights  # absolute kept
    assert cfg.log.dir.is_dir()


def test_overrides_dotted_keys(tmp_path):
    cfg = build_config({}, tmp_path, {"robot.backend": "stub", "loop.planning_horizon": 2})
    assert cfg.loop.planning_horizon == 2


def test_fault_override_with_real_backend_is_error(tmp_path):
    with pytest.raises(ConfigError, match="faults"):
        build_config({}, tmp_path, {
            "robot.backend": "real", "robot.network_interface": "eth0",
            "stub.faults": [{"step": 2, "kind": "hang"}],
        })


def test_example_config_is_valid(tmp_path):
    cfg = load_config(REPO_ROOT / "config.example.toml")
    assert cfg == build_config({}, REPO_ROOT)


def test_make_config(tmp_path):
    cfg = make_config(tmp_path, loop={"max_failures": 1})
    assert cfg.base_dir == REPO_ROOT
    assert cfg.skills.dir == REPO_ROOT / "skills"
    assert cfg.log.dir == tmp_path / "runs"
    assert cfg.stub.state_file.parent == tmp_path / "runs"
    assert cfg.stub.time_scale == 0.01
    assert cfg.loop.max_failures == 1
    assert cfg.loop.planning_horizon == 5


# --- validation rules (§5.2) --------------------------------------------------


def ok(tmp_path, data):
    return build_config(data, tmp_path)


def bad(tmp_path, data, match=None):
    with pytest.raises(ConfigError, match=match):
        build_config(data, tmp_path)


def test_real_backend_requires_interface(tmp_path):
    bad(tmp_path, {"robot": {"backend": "real"}}, "network_interface")
    bad(tmp_path, {"robot": {"backend": "real", "network_interface": "  "}}, "network_interface")
    ok(tmp_path, {"robot": {"backend": "real", "network_interface": "enp0s31f6"}})


def test_backend_value(tmp_path):
    bad(tmp_path, {"robot": {"backend": "sim"}}, "robot.backend")


def test_faults_empty_with_real_backend(tmp_path):
    bad(tmp_path, {"robot": {"backend": "real", "network_interface": "eth0"},
                   "stub": {"faults": [{"step": 1, "kind": "error"}]}}, "faults")


@pytest.mark.parametrize("kind", ["error", "hang", "crash", "garbage"])
def test_fault_kinds_valid(tmp_path, kind):
    ok(tmp_path, {"stub": {"faults": [{"step": 1, "kind": kind}]}})


@pytest.mark.parametrize("fault", [
    {"step": 1, "kind": "explode"},
    {"step": 0, "kind": "error"},
    {"step": -1, "kind": "error"},
    {"step": 1.0, "kind": "error"},
    {"step": "1", "kind": "error"},
    {"kind": "error"},
    {"step": 1, "kind": "error", "extra": 1},
])
def test_fault_invalid(tmp_path, fault):
    bad(tmp_path, {"stub": {"faults": [fault]}}, "stub.faults")


def test_fault_steps_unique(tmp_path):
    bad(tmp_path, {"stub": {"faults": [{"step": 2, "kind": "error"},
                                       {"step": 2, "kind": "hang"}]}}, "unique")


def test_detections(tmp_path):
    ok(tmp_path, {"stub": {"detections": {"chair": "center:near", "cell phone": "left:far",
                                          "person": "right:medium"}}})
    bad(tmp_path, {"stub": {"detections": {"phone": "left:far"}}}, "phone")
    bad(tmp_path, {"stub": {"detections": {"chair": "middle:near"}}}, "chair")
    bad(tmp_path, {"stub": {"detections": {"chair": "center:close"}}}, "chair")
    bad(tmp_path, {"stub": {"detections": {"chair": "center:near "}}}, "chair")


def test_initial_posture(tmp_path):
    ok(tmp_path, {"stub": {"initial_posture": "sitting"}})
    bad(tmp_path, {"stub": {"initial_posture": "unknown"}}, "initial_posture")


@pytest.mark.parametrize("section,key", [
    ("loop", "planning_horizon"), ("loop", "max_failures"), ("loop", "max_llm_calls"),
    ("loop", "context_history_k"), ("llm", "max_tokens"),
])
def test_integers_at_least_one(tmp_path, section, key):
    ok(tmp_path, {section: {key: 1}})
    bad(tmp_path, {section: {key: 0}}, f"{section}.{key}")
    bad(tmp_path, {section: {key: 2.5}}, f"{section}.{key}")
    bad(tmp_path, {section: {key: True}}, f"{section}.{key}")


@pytest.mark.parametrize("section,key", [
    ("loop", "task_time_limit_s"), ("llm", "request_timeout_s"),
    ("robot", "stop_move_timeout_s"), ("robot", "read_state_timeout_s"),
    ("stub", "time_scale"),
])
def test_floats_positive(tmp_path, section, key):
    ok(tmp_path, {section: {key: 0.001}})
    bad(tmp_path, {section: {key: 0}}, f"{section}.{key}")
    bad(tmp_path, {section: {key: -1.0}}, f"{section}.{key}")


@pytest.mark.parametrize("section,key", [
    ("motion_budget", "max_distance_m"), ("motion_budget", "max_rotation_deg"),
])
def test_floats_non_negative(tmp_path, section, key):
    ok(tmp_path, {section: {key: 0.0}})
    bad(tmp_path, {section: {key: -0.1}}, f"{section}.{key}")


def test_llm_defaults(tmp_path):
    cfg = make_config(tmp_path).llm
    assert cfg.model == "claude-sonnet-5-5"
    assert cfg.max_tokens == 2048 and cfg.thinking == "between_tools"


def test_llm_thinking(tmp_path):
    ok(tmp_path, {"llm": {"thinking": "between_tools"}})
    ok(tmp_path, {"llm": {"thinking": "adaptive"}})
    bad(tmp_path, {"llm": {"thinking": "disabled"}}, "thinking")
    bad(tmp_path, {"llm": {"thinking": "enabled"}}, "thinking")


def test_llm_temperature_removed(tmp_path):
    bad(tmp_path, {"llm": {"temperature": 0.0}}, "temperature")


def test_backoff_values_non_negative(tmp_path):
    ok(tmp_path, {"llm": {"infra_backoff_s": [0.0, 0.0]}})
    bad(tmp_path, {"llm": {"infra_backoff_s": [1.0, -1.0]}}, "infra_backoff_s")


def test_infra_retries(tmp_path):
    ok(tmp_path, {"llm": {"infra_max_retries": 0, "infra_backoff_s": []}})
    ok(tmp_path, {"llm": {"infra_max_retries": 1, "infra_backoff_s": [1.0, 2.0]}})
    bad(tmp_path, {"llm": {"infra_max_retries": -1}}, "infra_max_retries")
    bad(tmp_path, {"llm": {"infra_max_retries": 3, "infra_backoff_s": [1.0, 4.0]}},
        "infra_backoff_s")


def test_allowed_user_ids(tmp_path):
    ok(tmp_path, {"telegram": {"allowed_user_ids": [123, 456]}})
    bad(tmp_path, {"telegram": {"allowed_user_ids": ["123"]}}, "allowed_user_ids")


# --- .env ---------------------------------------------------------------------


def test_env_parsing():
    text = (
        "# comment\n"
        "\n"
        "A=1\n"
        "export B=two\n"
        "C=\"double quoted\"\n"
        "D='single quoted'\n"
        "E=\"mismatched'\n"
        "F=\"\"\"x\"\"\"\n"
        "  G = spaced  \n"
        "H=a=b\n"
        "I=$A\n"
        "  # indented comment\n"
        "no_equals_line\n"
        "J=\n"
    )
    env = parse_env_text(text)
    assert env == {
        "A": "1",
        "B": "two",
        "C": "double quoted",
        "D": "single quoted",
        "E": "\"mismatched'",
        "F": "\"\"x\"\"",
        "G": "spaced",
        "H": "a=b",
        "I": "$A",
        "J": "",
    }


def test_real_env_overrides_dotenv(tmp_path, monkeypatch):
    write(tmp_path / ".env", "GO2_T1_REAL=from_file\nGO2_T1_FILE_ONLY='from file'\n")
    monkeypatch.setenv("GO2_T1_REAL", "from_env")
    monkeypatch.delenv("GO2_T1_FILE_ONLY", raising=False)
    try:
        applied = load_env_file(tmp_path)
        assert os.environ["GO2_T1_REAL"] == "from_env"
        assert os.environ["GO2_T1_FILE_ONLY"] == "from file"
        assert applied == {"GO2_T1_FILE_ONLY": "from file"}
    finally:
        os.environ.pop("GO2_T1_FILE_ONLY", None)


def test_dotenv_missing_is_fine(tmp_path):
    assert load_env_file(tmp_path) == {}


def test_load_config_and_env_reads_dotenv_from_base_dir(tmp_path, monkeypatch):
    sub = tmp_path / "conf"
    sub.mkdir()
    write(sub / "config.toml", "")
    write(sub / ".env", "export GO2_T1_BASE=yes\n")
    monkeypatch.delenv("GO2_T1_BASE", raising=False)
    try:
        cfg = load_config_and_env(sub / "config.toml")
        assert cfg.base_dir == sub.resolve()
        assert os.environ["GO2_T1_BASE"] == "yes"
    finally:
        os.environ.pop("GO2_T1_BASE", None)
