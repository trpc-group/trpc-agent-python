"""Tests for Skills Hub remote-skill installation helpers."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from trpc_agent_sdk.skills import create_default_skill_repository
from trpc_agent_sdk.skills.hub import SkillSpec
from trpc_agent_sdk.skills.hub import SkillSpecsConfig
from trpc_agent_sdk.skills.hub import SkillBundle
from trpc_agent_sdk.skills.hub import SkillSource
from trpc_agent_sdk.skills.hub import sync_remote_skills
from trpc_agent_sdk.skills.hub._install import _fetch_remote_skill
from trpc_agent_sdk.skills.hub._install import _find_existing_skill_dirs
from trpc_agent_sdk.skills.hub._install import _write_bundle_files


class FakeSource(SkillSource):
    """Minimal in-memory SkillSource for install tests."""

    def __init__(self, bundle: SkillBundle | None = None, *, raise_on_fetch: bool = False):
        self.bundle = bundle
        self.raise_on_fetch = raise_on_fetch
        self.fetch_calls: list[str] = []

    def source_id(self) -> str:
        return "fake"

    def search(self, query: str, limit: int = 10) -> list:
        return []

    def inspect(self, identifier: str):
        return None

    def fetch(self, identifier: str) -> SkillBundle | None:
        self.fetch_calls.append(identifier)
        if self.raise_on_fetch:
            raise RuntimeError("network boom")
        return self.bundle


def _bundle(name: str = "plan", **metadata) -> SkillBundle:
    return SkillBundle(
        name=name,
        files={
            "SKILL.md": f"---\nname: {name}\ndescription: test skill\n---\nbody",
            "scripts/run.sh": "echo hi",
        },
        source="fake",
        identifier="whatever",
        metadata=metadata,
    )


class TestRemoteSkillValidation:

    def test_valid_name_is_accepted(self):
        remote_skill = SkillSpec(source=FakeSource(), identifier="id", name="plan")
        assert remote_skill.name == "plan"

    def test_unsafe_name_raises(self):
        with pytest.raises(ValueError):
            SkillSpec(source=FakeSource(), identifier="id", name="../escape")

    def test_nested_name_raises(self):
        with pytest.raises(ValueError):
            SkillSpec(source=FakeSource(), identifier="id", name="a/b")

    def test_defaults(self):
        remote_skill = SkillSpec(source=FakeSource(), identifier="id", name="plan")
        assert remote_skill.category is None
        assert remote_skill.replace_if_exists is False
        assert remote_skill.on_error == "skip"


class TestFindExistingSkillDirs:

    def test_missing_skills_path_returns_empty(self, tmp_path: Path):
        assert _find_existing_skill_dirs(tmp_path / "nonexistent", "plan") == []

    def test_finds_across_multiple_categories(self, tmp_path: Path):
        dir1 = tmp_path / "hub" / "plan"
        dir2 = tmp_path / "dev" / "plan"
        dir1.mkdir(parents=True)
        dir2.mkdir(parents=True)

        found = _find_existing_skill_dirs(tmp_path, "plan")

        assert sorted(found) == sorted([dir1, dir2])

    def test_ignores_hidden_category_dirs(self, tmp_path: Path):
        (tmp_path / ".tmp" / "plan").mkdir(parents=True)
        assert _find_existing_skill_dirs(tmp_path, "plan") == []


class TestWriteBundleFiles:

    def test_writes_text_and_bytes(self, tmp_path: Path):
        _write_bundle_files(
            skills_path=tmp_path,
            category="hub",
            name="plan",
            files={
                "SKILL.md": "body",
                "assets/logo.png": b"\x89PNG"
            },
        )

        target = tmp_path / "hub" / "plan"
        assert (target / "SKILL.md").read_text() == "body"
        assert (target / "assets" / "logo.png").read_bytes() == b"\x89PNG"

    def test_overwrites_existing_target_dir(self, tmp_path: Path):
        target = tmp_path / "hub" / "plan"
        target.mkdir(parents=True)
        (target / "stale.txt").write_text("old")

        _write_bundle_files(skills_path=tmp_path, category="hub", name="plan", files={"SKILL.md": "new"})

        assert not (target / "stale.txt").exists()
        assert (target / "SKILL.md").read_text() == "new"

    def test_rejects_unsafe_category(self, tmp_path: Path):
        with pytest.raises(ValueError):
            _write_bundle_files(skills_path=tmp_path, category="../escape", name="plan", files={"SKILL.md": "x"})

    def test_rejects_unsafe_bundle_path_and_cleans_up(self, tmp_path: Path):
        with pytest.raises(ValueError):
            _write_bundle_files(
                skills_path=tmp_path,
                category="hub",
                name="plan",
                files={"../escape.txt": "x"},
            )
        assert not (tmp_path / "hub" / "plan").exists()
        assert not (tmp_path / ".tmp").exists() or not any((tmp_path / ".tmp").iterdir())


class TestFetchRemoteSkill:

    def test_skips_when_already_installed(self, tmp_path: Path):
        (tmp_path / "hub" / "plan").mkdir(parents=True)
        source = FakeSource(bundle=_bundle())
        remote_skill = SkillSpec(source=source, identifier="id", name="plan")

        _fetch_remote_skill(remote_skill, tmp_path)

        assert source.fetch_calls == []

    def test_fetches_and_writes_when_missing(self, tmp_path: Path):
        source = FakeSource(bundle=_bundle())
        remote_skill = SkillSpec(source=source, identifier="fetch-me", name="plan", category="hub")

        _fetch_remote_skill(remote_skill, tmp_path)

        assert source.fetch_calls == ["fetch-me"]
        assert (tmp_path / "hub" / "plan" / "SKILL.md").exists()

    def test_raises_when_fetch_returns_none(self, tmp_path: Path):
        source = FakeSource(bundle=None)
        remote_skill = SkillSpec(source=source, identifier="missing", name="plan")

        with pytest.raises(ValueError, match="could not fetch"):
            _fetch_remote_skill(remote_skill, tmp_path)

    def test_replace_if_exists_refetches_and_overwrites(self, tmp_path: Path):
        existing = tmp_path / "hub" / "plan"
        existing.mkdir(parents=True)
        (existing / "OLD.md").write_text("old")

        source = FakeSource(bundle=_bundle())
        remote_skill = SkillSpec(source=source, identifier="id", name="plan", category="hub", replace_if_exists=True)

        _fetch_remote_skill(remote_skill, tmp_path)

        assert source.fetch_calls == ["id"]
        assert not (existing / "OLD.md").exists()
        assert (existing / "SKILL.md").exists()

    def test_category_resolution(self, tmp_path: Path):
        source = FakeSource(bundle=_bundle(category="dev"))
        remote_skill = SkillSpec(source=source, identifier="id", name="plan")

        _fetch_remote_skill(remote_skill, tmp_path)

        assert (tmp_path / "dev" / "plan" / "SKILL.md").exists()

    def test_explicit_category_wins_over_bundle_metadata(self, tmp_path: Path):
        source = FakeSource(bundle=_bundle(category="dev"))
        remote_skill = SkillSpec(source=source, identifier="id", name="plan", category="custom")

        _fetch_remote_skill(remote_skill, tmp_path)

        assert (tmp_path / "custom" / "plan" / "SKILL.md").exists()
        assert not (tmp_path / "dev").exists()


class TestSyncRemoteSkills:

    def test_empty_remote_skills_is_noop(self, tmp_path: Path):
        target = tmp_path / "skills"
        sync_remote_skills([], target)
        assert not target.exists()

    def test_creates_install_root(self, tmp_path: Path):
        target = tmp_path / "skills"
        source = FakeSource(bundle=_bundle())
        remote_skill = SkillSpec(source=source, identifier="id", name="plan")

        sync_remote_skills([remote_skill], target)

        assert target.is_dir()
        assert (target / "hub" / "plan" / "SKILL.md").exists()

    def test_on_error_skip_continues_to_next_remote_skill(self, tmp_path: Path):
        failing = FakeSource(bundle=None)
        succeeding = FakeSource(bundle=_bundle())
        remote_skills = [
            SkillSpec(source=failing, identifier="fails", name="broken", on_error="skip"),
            SkillSpec(source=succeeding, identifier="works", name="plan", on_error="skip"),
        ]

        sync_remote_skills(remote_skills, tmp_path)

        assert not (tmp_path / "hub" / "broken").exists()
        assert (tmp_path / "hub" / "plan" / "SKILL.md").exists()

    def test_on_error_raise_propagates_and_stops(self, tmp_path: Path):
        failing = FakeSource(bundle=None)
        succeeding = FakeSource(bundle=_bundle())
        remote_skills = [
            SkillSpec(source=failing, identifier="fails", name="broken", on_error="raise"),
            SkillSpec(source=succeeding, identifier="works", name="plan"),
        ]

        with pytest.raises(ValueError, match="could not fetch"):
            sync_remote_skills(remote_skills, tmp_path)

        assert succeeding.fetch_calls == []

    def test_fetch_exception_is_skipped_by_default(self, tmp_path: Path):
        source = FakeSource(raise_on_fetch=True)
        remote_skill = SkillSpec(source=source, identifier="id", name="plan")

        sync_remote_skills([remote_skill], tmp_path)

        assert not (tmp_path / "hub" / "plan").exists()


class TestCreateDefaultSkillRepositoryRemoteSkills:

    def test_additional_skill_specs_are_installed_and_indexed(self, tmp_path: Path):
        install_root = tmp_path / "downloaded"
        source = FakeSource(bundle=_bundle(name="plan"))

        repository = create_default_skill_repository(
            additional_skill_specs=SkillSpecsConfig(
                specs=[SkillSpec(source=source, identifier="id", name="plan")],
                install_path=str(install_root),
            ),
            use_cached_repository=False,
        )

        assert source.fetch_calls == ["id"]
        assert repository.skill_list() == ["plan"]
        assert repository.path("plan") == str(install_root / "hub" / "plan")

    def test_install_path_defaults_to_system_temp_dir(self):
        config = SkillSpecsConfig(specs=[SkillSpec(source=FakeSource(bundle=_bundle()), identifier="id",
                                                   name="plan")], )

        assert config.install_path == str(Path(tempfile.gettempdir()) / "trpc_agent_skills")

    def test_local_roots_precede_remote_install_root(self, tmp_path: Path):
        local_root = tmp_path / "local"
        local_skill = local_root / "local-category" / "plan"
        local_skill.mkdir(parents=True)
        (local_skill / "SKILL.md").write_text("---\nname: plan\ndescription: local\n---\nlocal body")

        install_root = tmp_path / "downloaded"
        source = FakeSource(bundle=_bundle(name="plan"))

        repository = create_default_skill_repository(
            str(local_root),
            additional_skill_specs=SkillSpecsConfig(
                specs=[SkillSpec(source=source, identifier="id", name="plan")],
                install_path=str(install_root),
            ),
            use_cached_repository=False,
        )

        assert repository.path("plan") == str(local_skill)
        assert (install_root / "hub" / "plan" / "SKILL.md").exists()
