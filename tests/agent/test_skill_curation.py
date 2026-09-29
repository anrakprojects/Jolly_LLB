"""Legal curation of bundled skills (agent.skill_utils.LEGAL_BUNDLED_SKILLS)."""

import os
from pathlib import Path

import pytest

from agent.skill_utils import (
    LEGAL_BUNDLED_SKILLS,
    get_curated_out_skill_names,
    get_disabled_skill_names,
)


@pytest.fixture
def hermes_home():
    home = Path(os.environ["HERMES_HOME"])
    (home / "skills").mkdir(parents=True, exist_ok=True)
    return home


def _write_skill(home: Path, category: str, name: str) -> None:
    skill_dir = home / "skills" / category / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} skill.\n---\n\n# {name}\n",
        encoding="utf-8",
    )


def _seed_bundled(home: Path, names) -> None:
    (home / "skills" / ".bundled_manifest").write_text(
        "".join(f"{n}:hash\n" for n in names), encoding="utf-8"
    )


def _write_config(home: Path, text: str) -> None:
    (home / "config.yaml").write_text(text, encoding="utf-8")


def _legal_name() -> str:
    return sorted(LEGAL_BUNDLED_SKILLS)[0]


def test_non_legal_bundled_skills_are_curated_out(hermes_home):
    legal = _legal_name()
    _seed_bundled(hermes_home, [legal, "pokemon-player"])

    assert get_curated_out_skill_names() == {"pokemon-player"}
    # Applies even with no config.yaml at all.
    assert "pokemon-player" in get_disabled_skill_names()
    assert legal not in get_disabled_skill_names()


def test_user_skills_are_never_curated(hermes_home):
    _seed_bundled(hermes_home, ["pokemon-player"])

    # Not in the bundled manifest -> user-added, always visible.
    assert "firm-style-guide" not in get_curated_out_skill_names()


def test_no_manifest_means_no_curation(hermes_home):
    assert get_curated_out_skill_names() == set()


def test_curation_can_be_disabled(hermes_home):
    _seed_bundled(hermes_home, ["pokemon-player"])
    _write_config(hermes_home, "skills:\n  curation:\n    enabled: false\n")

    assert get_curated_out_skill_names() == set()
    assert get_disabled_skill_names() == set()


def test_allow_bundled_restores_individual_skills(hermes_home):
    _seed_bundled(hermes_home, ["pokemon-player", "arxiv"])
    _write_config(hermes_home, "skills:\n  curation:\n    allow_bundled: [arxiv]\n")

    assert get_curated_out_skill_names() == {"pokemon-player"}


def test_curated_out_merges_with_user_disabled(hermes_home):
    _seed_bundled(hermes_home, ["pokemon-player"])
    _write_config(hermes_home, "skills:\n  disabled: [my-skill]\n")

    assert get_disabled_skill_names() == {"my-skill", "pokemon-player"}


def test_find_all_skills_hides_curated_even_when_listing_disabled(hermes_home):
    from tools import skills_tool

    legal = _legal_name()
    _write_skill(hermes_home, "legal", legal)
    _write_skill(hermes_home, "gaming", "pokemon-player")
    _write_skill(hermes_home, "custom", "firm-style-guide")
    _seed_bundled(hermes_home, [legal, "pokemon-player"])
    _write_config(hermes_home, "skills:\n  disabled: [firm-style-guide]\n")

    all_names = {s["name"] for s in skills_tool._find_all_skills(skip_disabled=True)}
    assert all_names == {legal, "firm-style-guide"}

    enabled_names = {s["name"] for s in skills_tool._find_all_skills()}
    assert enabled_names == {legal}


def test_skill_view_refuses_curated_out_skill(hermes_home):
    from tools import skills_tool

    _seed_bundled(hermes_home, ["pokemon-player"])

    assert skills_tool._is_skill_disabled("pokemon-player") is True
    assert skills_tool._is_skill_disabled("firm-style-guide") is False


def test_legal_allowlist_covers_skills_code_depends_on():
    # The kanban dispatcher/swarm load these by name.
    assert {"kanban-worker", "kanban-orchestrator"} <= LEGAL_BUNDLED_SKILLS


def test_legal_allowlist_names_exist_in_bundle():
    """Every allowlisted name must be a real bundled skill (catches typos/renames)."""
    from tools.skills_sync import _discover_bundled_skills, _get_bundled_dir

    bundled = {name for name, _ in _discover_bundled_skills(_get_bundled_dir())}
    assert LEGAL_BUNDLED_SKILLS <= bundled, LEGAL_BUNDLED_SKILLS - bundled
