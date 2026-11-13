"""SKILL.md parser: accepted keys, every schema error and the body template (contract/docs/contract.md).
Discovery, entrypoint and POLICY checks are the dispatcher registry's tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from contract.manifest import (
    BODY_MAX_CHARS,
    SECTION_HEADINGS,
    Expectation,
    SkillFileError,
    YawDelta,
    parse_skill_file,
)

HEAD = "name: demo\nentrypoint: skills.demo\ndescription: A demo skill.\n"
BODY = "## Use when\nAlways.\n\n## Requires\nNone.\n\n## Effect\nDoes it\nin two lines.\n\n## Limits\nNone.\n"


def write(tmp_path: Path, frontmatter: str, *, raw: bool = False, body: str = BODY) -> Path:
    """Write ``tmp_path/SKILL.md``. Unless ``raw``, name/entrypoint/description are prepended and ``body`` appended."""
    path = tmp_path / "SKILL.md"
    path.write_text(frontmatter if raw else f"---\n{HEAD}{frontmatter}---\n\n{body}", encoding="utf-8")
    return path


def assert_error(path: Path, match: str | None = None) -> None:
    with pytest.raises(SkillFileError) as ei:
        parse_skill_file(path)
    assert str(ei.value).startswith(str(path))
    if match:
        assert match in str(ei.value)


ANGLE = (
    "params:\n  a:\n    type: number\n    unit: degrees\n    description: A.\n"
    "  d:\n    type: enum\n    values: [left, right]\n    description: D.\n"
)


def test_expect_and_requires_parse(tmp_path):
    fm = (
        ANGLE
        + "expect:\n  posture: standing\n  mode: 0\n  yaw_delta: {param: a, direction_param: d, tolerance_deg: 10}\n"
    )
    f = parse_skill_file(write(tmp_path, fm + "requires:\n  posture: [standing, unknown]\n"))
    assert f.expect == Expectation(
        posture="standing", mode=0, yaw_delta=YawDelta(param="a", direction_param="d", tolerance_deg=10)
    )
    assert f.requires is not None and f.requires.posture == ("standing", "unknown")
    assert parse_skill_file(write(tmp_path, "expect: {self_reported: true}\n")).expect == Expectation(
        self_reported=True
    )
    bare = parse_skill_file(write(tmp_path, ""))
    assert bare.expect is None and bare.requires is None  # absent: unverified, no precondition


@pytest.mark.parametrize(
    ("frontmatter", "match"),
    [
        ("expect: {}\n", "at least one"),
        ("expect: {posture: unknown}\n", "expect.posture"),  # not a goal
        ("expect: {mode: true}\n", "expect.mode"),  # strict: a bool is not a mode
        ("expect: {body_height: 0.2}\n", "unknown key"),
        ("expect: {self_reported: true, mode: 0}\n", "self_reported must be the only key"),
        ("expect: {self_reported: false}\n", "expect.self_reported"),
        ("expect:\n", "must not be null"),
        (ANGLE + "expect: {yaw_delta: {param: a, direction_param: d, tolerance_deg: 0}}\n", "tolerance_deg"),
        (ANGLE + "expect: {yaw_delta: {param: b, direction_param: d, tolerance_deg: 5}}\n", "'b' is not a number"),
        (ANGLE + "expect: {yaw_delta: {param: a, tolerance_deg: 5}}\n", "direction_param"),
        (ANGLE + "expect: {yaw_delta: {param: a, direction_param: a, tolerance_deg: 5}}\n", "'a' is not an enum"),
        (
            ANGLE.replace("right", "up") + "expect: {yaw_delta: {param: a, direction_param: d, tolerance_deg: 5}}\n",
            "'d' is not an enum",
        ),
        ("requires: {posture: []}\n", "non-empty"),
        ("requires: {posture: [standing, standing]}\n", "unique"),
        ("requires: {posture: [[standing]]}\n", "must be strings"),
        ("requires: {posture: [lying]}\n", "requires.posture"),
    ],
)
def test_invalid_expect_or_requires(tmp_path, frontmatter, match):
    assert_error(write(tmp_path, frontmatter), match)


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


# --- body template ------------------------------------------------------------------


def test_body_sections_in_template_order(tmp_path):
    sf = parse_skill_file(write(tmp_path, ""))
    assert [s.heading for s in sf.sections] == list(SECTION_HEADINGS)
    assert sf.sections[2].text == "Does it\nin two lines."


@pytest.mark.parametrize(
    ("body", "match"),
    [
        (BODY.replace("## Requires\nNone.\n\n", ""), "body: missing section '## Requires'"),
        (BODY.replace("## Limits\nNone.\n", ""), "body: missing section '## Limits'"),
        (BODY.replace("## Limits", "## Notes"), "body: unknown heading '## Notes'"),
        (BODY.replace("## Limits", "### Limits"), "body: unknown heading '### Limits'"),
        ("# demo\n" + BODY, "body: unknown heading '# demo'"),
        ("Intro.\n" + BODY, "body: text outside a section: 'Intro.'"),
        (BODY + "## Effect\nAgain.\n", "heading '## Effect' is repeated or out of order"),
        (BODY.replace("## Limits\nNone.", "## Limits\n"), "section '## Limits' is empty; write 'None.'"),
        (BODY + "x" * BODY_MAX_CHARS, f"over the cap of {BODY_MAX_CHARS}"),
    ],
)
def test_body_template_errors(tmp_path, body, match):
    assert_error(write(tmp_path, "", body=body), match)


def test_sections_key_in_frontmatter_rejected(tmp_path):
    assert_error(write(tmp_path, "sections: []\n"), "sections: unknown key")
