"""SKILL.md parser: accepted keys, every schema error, the body template and its sync with code constants
(skills/docs/skills.md). Discovery, entrypoint and POLICY checks are the dispatcher registry's tests."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from skills import turn, walk
from skills.manifest import BODY_MAX_CHARS, SECTION_HEADINGS, SkillFileError, parse_skill_file

CATALOG = Path(__file__).resolve().parents[2] / "catalog"
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


def _number_before(text: str, unit: str) -> float:
    m = re.search(rf"(\d+(?:\.\d+)?) {re.escape(unit)}", text)
    assert m, f"no '<number> {unit}' in {text!r}"
    return float(m.group(1))


def test_prose_numbers_match_code_constants():
    def effect(name: str) -> str:
        sf = parse_skill_file(CATALOG / name / "SKILL.md")
        return next(s.text for s in sf.sections if s.heading == "Effect")

    assert _number_before(effect("walk"), "m/s") == walk.VELOCITY_MPS
    assert _number_before(effect("turn"), "rad/s") == turn.YAW_RATE_RPS
