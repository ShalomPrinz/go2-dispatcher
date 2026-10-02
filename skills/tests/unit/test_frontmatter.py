"""SKILL.md frontmatter parser: accepted keys and every schema error (skills/docs/skills.md).
Discovery, entrypoint and POLICY checks are the dispatcher registry's tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from skills.frontmatter import SkillFileError, parse_skill_file

HEAD = "name: demo\nentrypoint: skills.demo\ndescription: A demo skill.\n"


def write(tmp_path: Path, frontmatter: str, *, raw: bool = False) -> Path:
    """Write ``tmp_path/SKILL.md``. Unless ``raw``, name/entrypoint/description are prepended."""
    path = tmp_path / "SKILL.md"
    path.write_text(frontmatter if raw else f"---\n{HEAD}{frontmatter}---\n\n# body\n", encoding="utf-8")
    return path


def assert_error(path: Path, match: str | None = None) -> None:
    with pytest.raises(SkillFileError) as ei:
        parse_skill_file(path)
    assert str(ei.value).startswith(str(path))
    if match:
        assert match in str(ei.value)


def test_expect_key_accepted(tmp_path):
    assert parse_skill_file(write(tmp_path, "expect: {anything: [1, 2]}\n")).name == "demo"


@pytest.mark.parametrize(
    "text",
    [
        "# no frontmatter\n",
        "",
        "---\nname: demo\n",  # not closed
        "---\nname: [unclosed\n---\n",  # invalid YAML
        "---\n- a\n- b\n---\n",  # not a mapping
        "---\n---\n",  # empty
    ],
)
def test_missing_or_invalid_frontmatter(tmp_path, text):
    assert_error(write(tmp_path, text, raw=True))


@pytest.mark.parametrize("missing", ["name", "entrypoint", "description"])
def test_missing_required_key(tmp_path, missing):
    lines = [line for line in HEAD.splitlines() if not line.startswith(missing + ":")]
    assert_error(write(tmp_path, "---\n" + "\n".join(lines) + "\n---\n", raw=True), missing)


def test_name_bad_pattern(tmp_path):
    assert_error(write(tmp_path, "---\n" + HEAD.replace("demo", "Demo", 1) + "---\n", raw=True), "name")


@pytest.mark.parametrize(
    ("params", "match"),
    [
        ("params:\n  x:\n    type: float\n    description: X.\n", "params.x.type"),  # bad type
        ("params:\n  x:\n    type: number\n    description: X.\n    color: red\n", "color: unknown key"),
        ("params:\n  x:\n    type: enum\n    description: X.\n", "values"),  # enum without values
        ("params:\n  x:\n    type: string\n    min: 1\n    description: X.\n", "min"),  # min on string
        ("params:\n  x:\n    type: number\n    min: 5\n    max: 1\n    description: X.\n", "min must be <= max"),
        ("params:\n  Bad-Name:\n    type: number\n    description: X.\n", "Bad-Name"),
    ],
)
def test_invalid_param_spec(tmp_path, params, match):
    assert_error(write(tmp_path, params), match)


def test_explicit_null_and_set_rejected(tmp_path):
    for key, spec in [
        ("min", "type: number\n    min: null"),
        ("unit", "type: string\n    unit: null"),
        ("values", "type: string\n    values: null"),
    ]:
        assert_error(write(tmp_path, f"params:\n  x:\n    {spec}\n    description: X.\n"), f"params.x.{key}")
    path = write(tmp_path, "params:\n  x:\n    type: enum\n    values: !!set {a, b}\n    description: X.\n")
    assert_error(path, "values must be a list")


@pytest.mark.parametrize(
    "spec",
    [
        "type: number\n    min: 1\n    max: 5\n    default: 9",  # above max
        "type: number\n    default: true",  # bool is not a number
        "type: enum\n    values: [a, b]\n    default: c",  # not a value
        "type: string\n    default: null",  # explicit null is not "no default"
    ],
)
def test_default_failing_its_own_checks(tmp_path, spec):
    assert_error(write(tmp_path, f"params:\n  x:\n    {spec}\n    description: X.\n"), "default")
