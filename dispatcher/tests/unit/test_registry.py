"""Registry: loading, discovery and POLICY errors, catalog golden file, registry hash
(skills/docs/skills.md, tests/docs/testing.md)."""

from __future__ import annotations

from pathlib import Path

import pytest

from dispatcher.llm import plan_tool_schema
from dispatcher.models import RegistryError
from dispatcher.prompts import system_text
from dispatcher.registry import Registry, registry_hash
from skills.result import SkillPolicy
from tests.helpers import REPO_ROOT

SKILLS_DIR = REPO_ROOT / "skills" / "catalog"
GOLDEN_CATALOG = REPO_ROOT / "dispatcher" / "tests" / "golden" / "catalog.txt"
SKILL_MODULES = "dispatcher.tests.helpers.skill_modules"
GOOD_ENTRY = f"{SKILL_MODULES}.good"

GOOD_PARAMS = """params:
  speed:
    type: number
    min: 1
    max: 5
    default: 2
    unit: m/s
    description: How fast.
"""


def write_skill(
    root: Path,
    folder: str,
    frontmatter: str,
    *,
    name: str | None = None,
    entrypoint: str = GOOD_ENTRY,
    raw: bool = False,
) -> Path:
    """Write ``root/folder/SKILL.md``. Unless ``raw``, name/entrypoint/description are prepended."""
    d = root / folder
    d.mkdir(parents=True, exist_ok=True)
    path = d / "SKILL.md"
    if raw:
        text = frontmatter
    else:
        head = f"name: {name if name is not None else folder}\nentrypoint: {entrypoint}\ndescription: A demo skill.\n"
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
    assert walk.params["direction"].required
    assert not walk.params["distance_m"].required and walk.params["distance_m"].default == 0.9
    sit = reg.get("sit")
    assert sit is not None and sit.params == {}
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
    write_skill(
        tmp_path,
        "demo",
        """params:
  a:
    type: number
    min: 1
    unit: steps
    description: A.
  b:
    type: number
    max: 2.5
    description: B.
  c:
    type: number
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
""",
    )
    assert Registry.load(tmp_path).catalog_text() == (
        "demo: A demo skill.\n"
        "  - a (required): number, at least 1 steps. A.\n"
        "  - b (required): number, at most 2.5. B.\n"
        "  - c (required): number. C.\n"
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


# --- RegistryError cases -----------------------------------------------------------------


def test_zero_skills(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(RegistryError):
        Registry.load(tmp_path)


def test_missing_skills_dir(tmp_path):
    with pytest.raises(RegistryError):
        Registry.load(tmp_path / "absent")


def test_unknown_key(tmp_path):
    path = write_skill(tmp_path, "demo", "color: red\n")
    assert_registry_error(tmp_path, path, "color")


def test_name_not_folder_name(tmp_path):
    path = write_skill(tmp_path, "demo", "", name="other")
    assert_registry_error(tmp_path, path, "folder")


def test_entrypoint_not_importable(tmp_path):
    path = write_skill(tmp_path, "demo", "", entrypoint=f"{SKILL_MODULES}.does_not_exist")
    assert_registry_error(tmp_path, path, "importable")


def test_module_without_policy(tmp_path):
    path = write_skill(tmp_path, "demo", "", entrypoint=f"{SKILL_MODULES}.no_policy")
    assert_registry_error(tmp_path, path, "POLICY")


def test_policy_not_a_skill_policy(tmp_path):
    path = write_skill(tmp_path, "demo", "", entrypoint=f"{SKILL_MODULES}.not_a_policy")
    assert_registry_error(tmp_path, path, "POLICY")


def test_policy_name_mismatch(tmp_path):
    path = write_skill(tmp_path, "demo", "", entrypoint=f"{SKILL_MODULES}.wrong_name")
    assert_registry_error(tmp_path, path, "POLICY.name")
