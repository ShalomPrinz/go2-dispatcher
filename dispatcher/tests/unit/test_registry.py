"""Registry: loading, validation errors, catalog golden file, registry hash (docs/skills.md, testing.md)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dispatcher.models import RegistryError
from dispatcher.policies import SkillPolicy
from dispatcher.llm import plan_tool_schema
from dispatcher.prompts import system_text
from dispatcher.registry import MISSING, Registry, registry_hash
from helpers import REPO_ROOT

SKILLS_DIR = REPO_ROOT / "skills" / "catalog"
GOLDEN_CATALOG = REPO_ROOT / "dispatcher" / "tests" / "golden" / "catalog.txt"
GOOD_ENTRY = "helpers.skill_modules.good"

GOOD_PARAMS = """params:
  speed:
    type: number
    min: 1
    max: 5
    default: 2
    unit: m/s
    description: How fast.
"""


def write_skill(root: Path, folder: str, frontmatter: str, *, name: str | None = None,
                entrypoint: str = GOOD_ENTRY, raw: bool = False) -> Path:
    """Write ``root/folder/SKILL.md``. Unless ``raw``, name/entrypoint/description are prepended."""
    d = root / folder
    d.mkdir(parents=True, exist_ok=True)
    path = d / "SKILL.md"
    if raw:
        text = frontmatter
    else:
        head = (f"name: {name if name is not None else folder}\n"
                f"entrypoint: {entrypoint}\n"
                "description: A demo skill.\n")
        text = f"---\n{head}{frontmatter}---\n\n# body\n"
    path.write_text(text, encoding="utf-8")
    return path


def assert_registry_error(root: Path, path: Path, match: str | None = None) -> None:
    with pytest.raises(RegistryError) as ei:
        Registry.load(root)
    assert str(path) in str(ei.value)
    if match:
        assert match in str(ei.value)


# --- the real skill set ------------------------------------------------------------


def test_loads_five_skills_sorted():
    reg = Registry.load(SKILLS_DIR)
    assert reg.names() == ["detect_object", "sit", "stretch", "turn", "walk"]
    walk = reg.get("walk")
    assert walk is not None and walk.entrypoint == "skills.walk"
    assert isinstance(walk.policy, SkillPolicy) and walk.policy.name == "walk"
    assert list(walk.params) == ["direction", "distance_m"]
    assert walk.params["direction"].default is MISSING
    assert walk.params["distance_m"].default == 0.9
    assert reg.get("sit").params == {}
    assert reg.get("nope") is None


def test_catalog_matches_golden(update_golden):
    text = Registry.load(SKILLS_DIR).catalog_text()
    if update_golden:
        GOLDEN_CATALOG.write_text(text, encoding="utf-8")
    assert text == GOLDEN_CATALOG.read_text(encoding="utf-8")


def test_registry_hash_stable_and_horizon_sensitive():
    cat = Registry.load(SKILLS_DIR).catalog_text()

    def h(horizon, sys=None):
        return registry_hash(sys or system_text(horizon), cat, plan_tool_schema(horizon))

    h1 = h(5)
    assert h1 == h(5)
    assert len(h1) == 16 and all(c in "0123456789abcdef" for c in h1)
    assert h1 != h(3)
    # the schema alone (maxItems) changes the hash
    assert h1 != registry_hash(system_text(5), cat, plan_tool_schema(3))
    assert h1 != h(5, sys="other")


# --- catalog rendering for every type phrase -------------------------------------------


def test_catalog_type_phrases(tmp_path):
    write_skill(tmp_path, "demo", """params:
  a:
    type: integer
    min: 1
    unit: steps
    description: A.
  b:
    type: number
    max: 2.5
    description: B.
  c:
    type: integer
    description: C.
  d:
    type: string
    default: hi
    description: D.
  e:
    type: enum
    values: [x, y]
    default: y
    description: E.
  f:
    type: number
    min: 0
    max: 10
    default: 3
    description: F.
""")
    assert Registry.load(tmp_path).catalog_text() == (
        "demo: A demo skill.\n"
        "  - a (required): integer, at least 1 steps. A.\n"
        "  - b (required): number, at most 2.5. B.\n"
        "  - c (required): integer. C.\n"
        "  - d (optional, default hi): text. D.\n"
        "  - e (optional, default y): one of x, y. E.\n"
        "  - f (optional, default 3): number from 0 to 10. F."
    )


def test_ignores_other_folders_and_files(tmp_path):
    write_skill(tmp_path, "demo", GOOD_PARAMS)
    (tmp_path / "notes").mkdir()
    (tmp_path / "README.md").write_text("x")
    (tmp_path / "SKILL.md").write_text("not a skill")
    assert Registry.load(tmp_path).names() == ["demo"]


def test_expect_key_accepted(tmp_path):
    write_skill(tmp_path, "demo", "expect: {anything: [1, 2]}\n")
    assert Registry.load(tmp_path).names() == ["demo"]


# --- RegistryError cases -----------------------------------------------------------------


def test_zero_skills(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(RegistryError):
        Registry.load(tmp_path)


def test_missing_skills_dir(tmp_path):
    with pytest.raises(RegistryError):
        Registry.load(tmp_path / "absent")


@pytest.mark.parametrize("text", [
    "# no frontmatter\n",
    "",
    "---\nname: demo\n",                       # not closed
    "---\nname: [unclosed\n---\n",             # invalid YAML
    "---\n- a\n- b\n---\n",                     # not a mapping
    "---\n---\n",                               # empty
])
def test_missing_or_invalid_frontmatter(tmp_path, text):
    path = write_skill(tmp_path, "demo", text, raw=True)
    assert_registry_error(tmp_path, path)


def test_unknown_key(tmp_path):
    path = write_skill(tmp_path, "demo", "color: red\n")
    assert_registry_error(tmp_path, path, "color")


@pytest.mark.parametrize("missing", ["name", "entrypoint", "description"])
def test_missing_required_key(tmp_path, missing):
    lines = {"name": "name: demo", "entrypoint": f"entrypoint: {GOOD_ENTRY}",
             "description": "description: A demo skill."}
    del lines[missing]
    path = write_skill(tmp_path, "demo", "---\n" + "\n".join(lines.values()) + "\n---\n", raw=True)
    assert_registry_error(tmp_path, path, missing)


def test_name_not_folder_name(tmp_path):
    path = write_skill(tmp_path, "demo", "", name="other")
    assert_registry_error(tmp_path, path, "folder")


def test_name_bad_pattern(tmp_path):
    path = write_skill(tmp_path, "Demo", "", name="Demo")
    assert_registry_error(tmp_path, path)


@pytest.mark.parametrize("params", [
    "params: [a, b]\n",                                                     # not a mapping
    "params:\n  x: 3\n",                                                    # spec not a mapping
    "params:\n  x:\n    description: X.\n",                                 # missing type
    "params:\n  x:\n    type: number\n",                                    # missing description
    "params:\n  x:\n    type: float\n    description: X.\n",                # bad type
    "params:\n  x:\n    type: number\n    description: X.\n    color: red\n",  # unknown key
    "params:\n  x:\n    type: enum\n    description: X.\n",                 # enum without values
    "params:\n  x:\n    type: enum\n    values: []\n    description: X.\n",  # empty values
    "params:\n  x:\n    type: enum\n    values: [Left]\n    description: X.\n",  # not lowercase
    "params:\n  x:\n    type: enum\n    values: [1, 2]\n    description: X.\n",  # not strings
    "params:\n  x:\n    type: string\n    values: [a]\n    description: X.\n",  # values on non-enum
    "params:\n  x:\n    type: string\n    min: 1\n    description: X.\n",   # min on string
    "params:\n  x:\n    type: enum\n    values: [a]\n    unit: m\n    description: X.\n",  # unit on enum
    "params:\n  x:\n    type: number\n    min: low\n    description: X.\n",  # non-numeric min
    "params:\n  x:\n    type: number\n    min: 5\n    max: 1\n    description: X.\n",  # min > max
    "params:\n  Bad-Name:\n    type: number\n    description: X.\n",        # bad param name
])
def test_invalid_param_spec(tmp_path, params):
    path = write_skill(tmp_path, "demo", params)
    assert_registry_error(tmp_path, path)


@pytest.mark.parametrize("spec", [
    "type: number\n    min: 1\n    max: 5\n    default: 9",      # above max
    "type: number\n    min: 1\n    default: 0",                  # below min
    "type: number\n    default: fast",                           # wrong type
    "type: number\n    default: true",                           # bool is not a number
    "type: integer\n    default: 2.5",                           # not an integer
    "type: enum\n    values: [a, b]\n    default: c",            # not a value
    "type: string\n    default: ''",                             # empty string
    "type: string\n    default: null",                           # null
])
def test_default_failing_its_own_checks(tmp_path, spec):
    path = write_skill(tmp_path, "demo", f"params:\n  x:\n    {spec}\n    description: X.\n")
    assert_registry_error(tmp_path, path, "default")


def test_entrypoint_not_importable(tmp_path):
    path = write_skill(tmp_path, "demo", "", entrypoint="helpers.skill_modules.does_not_exist")
    assert_registry_error(tmp_path, path, "importable")


def test_module_without_policy(tmp_path):
    path = write_skill(tmp_path, "demo", "", entrypoint="helpers.skill_modules.no_policy")
    assert_registry_error(tmp_path, path, "POLICY")


def test_policy_not_a_skill_policy(tmp_path):
    path = write_skill(tmp_path, "demo", "", entrypoint="helpers.skill_modules.not_a_policy")
    assert_registry_error(tmp_path, path, "POLICY")


def test_policy_name_mismatch(tmp_path):
    path = write_skill(tmp_path, "demo", "", entrypoint="helpers.skill_modules.wrong_name")
    assert_registry_error(tmp_path, path, "POLICY.name")
