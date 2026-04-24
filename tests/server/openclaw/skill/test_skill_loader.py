"""Unit tests for trpc_agent_sdk.server.openclaw.skill._skill_loader."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from trpc_agent_sdk.server.openclaw.config import ClawConfig, SkillConfig, SkillRootConfig
from trpc_agent_sdk.server.openclaw.skill._skill_loader import ClawSkillLoader


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_loader(tmp_path: Path, **overrides) -> ClawSkillLoader:
    """Instantiate ClawSkillLoader with a fully mocked __init__."""
    loader = object.__new__(ClawSkillLoader)
    loader._workspace_root = tmp_path / "workspace"
    loader._workspace_skills_root = loader._workspace_root / "skills"
    loader._workspace_skills_root.mkdir(parents=True, exist_ok=True)
    loader._downloaded_skills_root = loader._workspace_skills_root / "downloaded"
    loader._downloaded_skills_root.mkdir(parents=True, exist_ok=True)
    loader._bundled_root = overrides.get("bundled_root", str(tmp_path / "bundled"))
    loader._skill_configs = overrides.get("skill_configs", {})
    loader._eligible = overrides.get("eligible", set())
    loader._reasons = overrides.get("reasons", {})
    loader._skill_meta = overrides.get("skill_meta", {})
    loader._skill_has_openclaw_meta = overrides.get("skill_has_openclaw_meta", {})
    loader._skill_key_map = overrides.get("skill_key_map", {})
    loader._skill_env_vars = overrides.get("skill_env_vars", {})
    loader._skill_source_by_name = overrides.get("skill_source_by_name", {})
    loader._skill_original_dir_by_name = overrides.get("skill_original_dir_by_name", {})
    loader._skill_paths = overrides.get("skill_paths", {})
    loader._all_descriptions = overrides.get("all_descriptions", {})
    loader._claw_parser = MagicMock()
    loader.skills_cfg = overrides.get("skills_cfg", SkillRootConfig())
    return loader


def _create_skill_dir(base: Path, name: str = "my-skill") -> Path:
    d = base / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text("---\nname: test\n---\nBody")
    return d


# ---------------------------------------------------------------------------
# _discover_skill_dirs
# ---------------------------------------------------------------------------

class TestDiscoverSkillDirs:

    def test_empty_dir(self, tmp_path):
        loader = _make_loader(tmp_path)
        empty = tmp_path / "empty"
        empty.mkdir()
        assert loader._discover_skill_dirs(empty) == []

    def test_dir_with_skill_md(self, tmp_path):
        loader = _make_loader(tmp_path)
        root = tmp_path / "root"
        root.mkdir()
        (root / "SKILL.md").write_text("# Skill")
        result = loader._discover_skill_dirs(root)
        assert root.resolve() in result

    def test_nested_skill_md(self, tmp_path):
        loader = _make_loader(tmp_path)
        root = tmp_path / "root"
        child = root / "sub" / "deep"
        child.mkdir(parents=True)
        (child / "SKILL.md").write_text("# Skill")
        result = loader._discover_skill_dirs(root)
        assert child.resolve() in result

    def test_non_existent_dir(self, tmp_path):
        loader = _make_loader(tmp_path)
        missing = tmp_path / "does_not_exist"
        assert loader._discover_skill_dirs(missing) == []

    def test_lowercase_skill_md(self, tmp_path):
        loader = _make_loader(tmp_path)
        root = tmp_path / "root"
        root.mkdir()
        (root / "skill.md").write_text("# Skill")
        result = loader._discover_skill_dirs(root)
        assert root.resolve() in result

    def test_results_sorted(self, tmp_path):
        loader = _make_loader(tmp_path)
        root = tmp_path / "root"
        for name in ("zzz", "aaa", "mmm"):
            d = root / name
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text("# Skill")
        result = loader._discover_skill_dirs(root)
        assert result == sorted(result)


# ---------------------------------------------------------------------------
# _safe_link_name
# ---------------------------------------------------------------------------

class TestSafeLinkName:

    def test_normal_name(self, tmp_path):
        loader = _make_loader(tmp_path)
        src = Path("/some/path/my-skill")
        name = loader._safe_link_name(src, tmp_path)
        assert name == "my-skill"

    def test_special_chars_sanitized(self, tmp_path):
        loader = _make_loader(tmp_path)
        src = Path("/some/path/my skill!@#$%")
        name = loader._safe_link_name(src, tmp_path)
        assert "!" not in name
        assert "@" not in name
        assert "#" not in name
        assert " " not in name

    def test_empty_name_defaults_to_skill(self, tmp_path):
        loader = _make_loader(tmp_path)
        src = Path("/some/path/   ")
        name = loader._safe_link_name(src, tmp_path)
        assert name == "skill"

    def test_collision_adds_suffix(self, tmp_path):
        loader = _make_loader(tmp_path)
        (tmp_path / "my-skill").mkdir()
        src = Path("/some/path/my-skill")
        name = loader._safe_link_name(src, tmp_path)
        assert name == "my-skill-1"

    def test_multiple_collisions(self, tmp_path):
        loader = _make_loader(tmp_path)
        (tmp_path / "my-skill").mkdir()
        (tmp_path / "my-skill-1").mkdir()
        src = Path("/some/path/my-skill")
        name = loader._safe_link_name(src, tmp_path)
        assert name == "my-skill-2"


# ---------------------------------------------------------------------------
# _normalize_skill_meta
# ---------------------------------------------------------------------------

class TestNormalizeSkillMeta:

    def test_empty_raw(self, tmp_path):
        loader = _make_loader(tmp_path)
        result = loader._normalize_skill_meta({})
        assert result["skill_key"] == ""
        assert result["always"] is False
        assert result["os"] == []
        assert result["requires"]["bins"] == []
        assert result["install"] == ""

    def test_full_data(self, tmp_path):
        loader = _make_loader(tmp_path)
        raw = {
            "skill_key": "my.key",
            "always": True,
            "os": ["linux", "darwin"],
            "requires": {
                "bins": ["git", "curl"],
                "any_bins": ["magick", "convert"],
                "env": ["MY_KEY"],
                "config": ["my.config"],
            },
            "install": "brew install something",
        }
        result = loader._normalize_skill_meta(raw)
        assert result["skill_key"] == "my.key"
        assert result["always"] is True
        assert result["os"] == ["linux", "darwin"]
        assert result["requires"]["bins"] == ["git", "curl"]
        assert result["requires"]["any_bins"] == ["magick", "convert"]
        assert result["requires"]["env"] == ["MY_KEY"]
        assert result["requires"]["config"] == ["my.config"]
        assert result["install"] == "brew install something"

    def test_missing_requires_key(self, tmp_path):
        loader = _make_loader(tmp_path)
        raw = {"skill_key": "k"}
        result = loader._normalize_skill_meta(raw)
        assert result["requires"]["bins"] == []
        assert result["requires"]["any_bins"] == []

    def test_none_raw(self, tmp_path):
        loader = _make_loader(tmp_path)
        result = loader._normalize_skill_meta(None)
        assert result["skill_key"] == ""

    def test_requires_not_dict(self, tmp_path):
        loader = _make_loader(tmp_path)
        raw = {"requires": "invalid"}
        result = loader._normalize_skill_meta(raw)
        assert result["requires"]["bins"] == []


# ---------------------------------------------------------------------------
# _is_bundled_skill
# ---------------------------------------------------------------------------

class TestIsBundledSkill:

    def test_under_bundled_root(self, tmp_path):
        bundled = tmp_path / "bundled"
        bundled.mkdir()
        skill_dir = bundled / "my-skill"
        skill_dir.mkdir()
        loader = _make_loader(tmp_path, bundled_root=str(bundled))
        assert loader._is_bundled_skill(str(skill_dir)) is True

    def test_not_under_bundled_root(self, tmp_path):
        bundled = tmp_path / "bundled"
        bundled.mkdir()
        other = tmp_path / "other" / "my-skill"
        other.mkdir(parents=True)
        loader = _make_loader(tmp_path, bundled_root=str(bundled))
        assert loader._is_bundled_skill(str(other)) is False

    def test_empty_bundled_root(self, tmp_path):
        loader = _make_loader(tmp_path, bundled_root="")
        assert loader._is_bundled_skill(str(tmp_path / "anything")) is False

    def test_same_dir_returns_false(self, tmp_path):
        bundled = tmp_path / "bundled"
        bundled.mkdir()
        loader = _make_loader(tmp_path, bundled_root=str(bundled))
        assert loader._is_bundled_skill(str(bundled)) is False

    def test_empty_base_dir(self, tmp_path):
        loader = _make_loader(tmp_path, bundled_root=str(tmp_path))
        assert loader._is_bundled_skill("") is False


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------

class TestGet:

    def test_eligible_skill(self, tmp_path):
        base_dir = str(tmp_path / "skill_base")
        mock_skill = MagicMock()
        mock_skill.body = "Use {BASE_DIR}/script.sh"
        mock_skill.resources = []

        loader = _make_loader(
            tmp_path,
            eligible={"my-skill"},
            skill_paths={"my-skill": base_dir},
        )
        with patch.object(type(loader).__bases__[0], "get", return_value=mock_skill):
            result = loader.get("my-skill")
        assert base_dir in result.body

    def test_not_eligible_with_reason(self, tmp_path):
        loader = _make_loader(
            tmp_path,
            eligible=set(),
            reasons={"my-skill": "missing binary"},
        )
        with pytest.raises(ValueError, match="disabled.*missing binary"):
            loader.get("my-skill")

    def test_not_eligible_no_reason(self, tmp_path):
        loader = _make_loader(tmp_path, eligible=set(), reasons={})
        with pytest.raises(ValueError, match="disabled"):
            loader.get("my-skill")

    def test_empty_name(self, tmp_path):
        loader = _make_loader(tmp_path)
        with pytest.raises(ValueError, match="empty skill name"):
            loader.get("")

    def test_whitespace_name(self, tmp_path):
        loader = _make_loader(tmp_path)
        with pytest.raises(ValueError, match="empty skill name"):
            loader.get("   ")

    def test_replaces_double_brace_placeholder(self, tmp_path):
        base_dir = str(tmp_path / "skill_base")
        mock_skill = MagicMock()
        mock_skill.body = "Use {{BASE_DIR}}/script.sh"
        mock_skill.resources = []

        loader = _make_loader(
            tmp_path,
            eligible={"my-skill"},
            skill_paths={"my-skill": base_dir},
        )
        with patch.object(type(loader).__bases__[0], "get", return_value=mock_skill):
            result = loader.get("my-skill")
        assert base_dir in result.body
        assert "{{BASE_DIR}}" not in result.body


# ---------------------------------------------------------------------------
# path
# ---------------------------------------------------------------------------

class TestPath:

    def test_eligible(self, tmp_path):
        loader = _make_loader(tmp_path, eligible={"my-skill"})
        with patch.object(type(loader).__bases__[0], "path", return_value="/some/path"):
            assert loader.path("my-skill") == "/some/path"

    def test_not_eligible(self, tmp_path):
        loader = _make_loader(
            tmp_path,
            eligible=set(),
            reasons={"my-skill": "not available"},
        )
        with pytest.raises(ValueError, match="disabled"):
            loader.path("my-skill")

    def test_empty_name(self, tmp_path):
        loader = _make_loader(tmp_path)
        with pytest.raises(ValueError, match="empty skill name"):
            loader.path("")


# ---------------------------------------------------------------------------
# skill_run_env
# ---------------------------------------------------------------------------

class TestSkillRunEnv:

    def test_existing_skill(self, tmp_path):
        loader = _make_loader(
            tmp_path,
            skill_env_vars={"my-skill": {"KEY": "VALUE"}},
        )
        result = loader.skill_run_env("my-skill")
        assert result == {"KEY": "VALUE"}

    def test_returns_copy(self, tmp_path):
        env = {"KEY": "VALUE"}
        loader = _make_loader(tmp_path, skill_env_vars={"my-skill": env})
        result = loader.skill_run_env("my-skill")
        result["NEW"] = "OTHER"
        assert "NEW" not in loader._skill_env_vars["my-skill"]

    def test_empty_name(self, tmp_path):
        loader = _make_loader(tmp_path)
        assert loader.skill_run_env("") == {}

    def test_not_found(self, tmp_path):
        loader = _make_loader(tmp_path)
        assert loader.skill_run_env("nonexistent") == {}


# ---------------------------------------------------------------------------
# set_skill_enabled
# ---------------------------------------------------------------------------

class TestSetSkillEnabled:

    def test_enables_skill(self, tmp_path):
        loader = _make_loader(tmp_path, skill_configs={})
        loader.skills_cfg = SkillRootConfig()
        with patch.object(loader, "refresh"):
            loader.set_skill_enabled("my-key", True)
        assert loader._skill_configs["my-key"].enabled is True

    def test_disables_skill(self, tmp_path):
        loader = _make_loader(
            tmp_path,
            skill_configs={"my-key": SkillConfig(enabled=True)},
        )
        loader.skills_cfg = SkillRootConfig()
        with patch.object(loader, "refresh"):
            loader.set_skill_enabled("my-key", False)
        assert loader._skill_configs["my-key"].enabled is False

    def test_empty_key_raises(self, tmp_path):
        loader = _make_loader(tmp_path)
        with pytest.raises(ValueError, match="required"):
            loader.set_skill_enabled("", True)

    def test_calls_refresh(self, tmp_path):
        loader = _make_loader(tmp_path, skill_configs={})
        loader.skills_cfg = SkillRootConfig()
        with patch.object(loader, "refresh") as mock_refresh:
            loader.set_skill_enabled("my-key", True)
        mock_refresh.assert_called_once()


# ---------------------------------------------------------------------------
# user_prompt
# ---------------------------------------------------------------------------

class TestUserPrompt:

    def test_no_disabled(self, tmp_path):
        loader = _make_loader(tmp_path, reasons={})
        assert loader.user_prompt() == ""

    def test_some_disabled(self, tmp_path):
        loader = _make_loader(
            tmp_path,
            reasons={"skill-a": "missing git", "skill-b": "no env"},
        )
        prompt = loader.user_prompt()
        assert "skill-a" in prompt
        assert "skill-b" in prompt
        assert "missing git" in prompt
        assert "no env" in prompt
        assert prompt.startswith("# Skills")

    def test_sorted_output(self, tmp_path):
        loader = _make_loader(
            tmp_path,
            reasons={"zzz": "reason z", "aaa": "reason a"},
        )
        prompt = loader.user_prompt()
        assert prompt.index("aaa") < prompt.index("zzz")


# ---------------------------------------------------------------------------
# dependency_sources
# ---------------------------------------------------------------------------

class TestDependencySources:

    def test_all_skills(self, tmp_path):
        loader = _make_loader(
            tmp_path,
            skill_meta={"skill-a": {"requires": {"bins": ["git"]}, "install": "brew install git"}},
            skill_has_openclaw_meta={"skill-a": True},
            all_descriptions={"skill-a": "A skill"},
        )
        result = loader.dependency_sources()
        assert len(result) == 1
        assert result[0]["name"] == "skill-a"

    def test_selected_skills(self, tmp_path):
        loader = _make_loader(
            tmp_path,
            skill_meta={
                "skill-a": {"requires": {}, "install": ""},
                "skill-b": {"requires": {}, "install": ""},
            },
            skill_has_openclaw_meta={"skill-a": True, "skill-b": True},
            all_descriptions={"skill-a": "A", "skill-b": "B"},
        )
        result = loader.dependency_sources(["skill-a"])
        assert len(result) == 1
        assert result[0]["name"] == "skill-a"

    def test_unknown_skill_raises(self, tmp_path):
        loader = _make_loader(
            tmp_path,
            skill_meta={},
            skill_has_openclaw_meta={},
            all_descriptions={},
        )
        with pytest.raises(ValueError, match="unknown skill"):
            loader.dependency_sources(["nonexistent"])

    def test_skips_no_openclaw_meta(self, tmp_path):
        loader = _make_loader(
            tmp_path,
            skill_meta={"skill-a": {"requires": {}, "install": ""}},
            skill_has_openclaw_meta={"skill-a": False},
            all_descriptions={"skill-a": "A"},
        )
        result = loader.dependency_sources()
        assert len(result) == 0


# ---------------------------------------------------------------------------
# skill_list
# ---------------------------------------------------------------------------

class TestSkillList:

    def _make_loader_with_skills(self, tmp_path):
        loader = _make_loader(
            tmp_path,
            skill_paths={"enabled-skill": "/a", "disabled-skill": "/b"},
            eligible={"enabled-skill"},
            reasons={"disabled-skill": "missing bin"},
            skill_meta={
                "enabled-skill": {"skill_key": "e", "always": False, "requires": {}, "install": ""},
                "disabled-skill": {"skill_key": "d", "always": False, "requires": {}, "install": ""},
            },
            all_descriptions={"enabled-skill": "Enabled", "disabled-skill": "Disabled"},
            skill_source_by_name={"enabled-skill": "workspace", "disabled-skill": "builtin"},
        )
        return loader

    def test_all_mode(self, tmp_path):
        loader = self._make_loader_with_skills(tmp_path)
        result = loader.skill_list("all")
        assert result["mode"] == "all"
        assert result["total"] == 2
        assert result["enabled_count"] == 1
        assert result["disabled_count"] == 1
        assert len(result["entries"]) == 2

    def test_enabled_mode(self, tmp_path):
        loader = self._make_loader_with_skills(tmp_path)
        result = loader.skill_list("enabled")
        assert result["mode"] == "enabled"
        names = [e["name"] for e in result["entries"]]
        assert "enabled-skill" in names
        assert "disabled-skill" not in names

    def test_disabled_mode(self, tmp_path):
        loader = self._make_loader_with_skills(tmp_path)
        result = loader.skill_list("disabled")
        assert result["mode"] == "disabled"
        names = [e["name"] for e in result["entries"]]
        assert "disabled-skill" in names
        assert "enabled-skill" not in names

    def test_invalid_mode_defaults_to_all(self, tmp_path):
        loader = self._make_loader_with_skills(tmp_path)
        result = loader.skill_list("invalid")
        assert result["mode"] == "all"
        assert len(result["entries"]) == 2

    def test_none_mode_defaults_to_all(self, tmp_path):
        loader = self._make_loader_with_skills(tmp_path)
        result = loader.skill_list(None)
        assert result["mode"] == "all"


# ---------------------------------------------------------------------------
# _normalize_skills_config (static)
# ---------------------------------------------------------------------------

class TestNormalizeSkillsConfig:

    def test_normalizes_fields(self, tmp_path):
        cfg = SkillRootConfig(
            config_keys=["  KEY1  ", "key2"],
            allow_bundled=["  Alpha  ", "beta"],
            skill_configs={" sk ": SkillConfig(enabled=True)},
            bundled_root="/some/path",
            skill_roots=["  /a  ", "", "  /b  "],
            builtin_skill_roots=["  /c  "],
        )
        ClawSkillLoader._normalize_skills_config(cfg)
        assert cfg.skill_roots == ["/a", "/b"]
        assert cfg.builtin_skill_roots == ["/c"]

    def test_empty_config(self):
        cfg = SkillRootConfig()
        ClawSkillLoader._normalize_skills_config(cfg)
        assert cfg.skill_roots == []
        assert cfg.builtin_skill_roots == []


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------

class TestInit:

    @patch("trpc_agent_sdk.server.openclaw.skill._skill_loader.FsSkillRepository.__init__", return_value=None)
    def test_init_sets_workspace_dirs(self, mock_super_init, tmp_path):
        workspace = tmp_path / "ws"
        workspace.mkdir()
        config = MagicMock(spec=ClawConfig)
        config.agent = MagicMock()
        config.agent.workspace = str(workspace)
        config.skills = SkillRootConfig()
        loader = ClawSkillLoader(config)
        assert loader._workspace_root == workspace.resolve()
        assert loader._workspace_skills_root.exists()
        mock_super_init.assert_called_once()


# ---------------------------------------------------------------------------
# Property accessors
# ---------------------------------------------------------------------------

class TestPropertyAccessors:

    def test_workspace_skills_root(self, tmp_path):
        loader = _make_loader(tmp_path)
        assert loader.workspace_skills_root == loader._workspace_skills_root

    def test_downloaded_skills_root(self, tmp_path):
        loader = _make_loader(tmp_path)
        assert loader.downloaded_skills_root == loader._downloaded_skills_root

    def test_eligible_set(self, tmp_path):
        eligible = {"skill-a", "skill-b"}
        loader = _make_loader(tmp_path, eligible=eligible)
        assert loader.eligible_set is eligible
        assert loader.eligible_set == {"skill-a", "skill-b"}

    def test_reasons(self, tmp_path):
        reasons = {"skill-a": "missing bin"}
        loader = _make_loader(tmp_path, reasons=reasons)
        assert loader.reasons is reasons

    def test_skill_meta(self, tmp_path):
        meta = {"skill-a": {"skill_key": "k1"}}
        loader = _make_loader(tmp_path, skill_meta=meta)
        assert loader.skill_meta is meta

    def test_skill_key_map(self, tmp_path):
        key_map = {"skill-a": "k1"}
        loader = _make_loader(tmp_path, skill_key_map=key_map)
        assert loader.skill_key_map is key_map

    def test_set_workspace_runtime(self, tmp_path):
        loader = _make_loader(tmp_path)
        mock_runtime = MagicMock()
        loader.set_workspace_runtime(mock_runtime)
        assert loader._workspace_runtime is mock_runtime


# ---------------------------------------------------------------------------
# _link_to_base
# ---------------------------------------------------------------------------

class TestLinkToBase:

    def test_creates_symlink(self, tmp_path):
        loader = _make_loader(tmp_path)
        src = tmp_path / "source_skill"
        src.mkdir()
        base = tmp_path / "link_base"
        base.mkdir()
        result = loader._link_to_base(src, base)
        assert result.is_symlink()
        assert result.resolve() == src.resolve()

    def test_creates_base_dir_if_missing(self, tmp_path):
        loader = _make_loader(tmp_path)
        src = tmp_path / "source_skill"
        src.mkdir()
        base = tmp_path / "nonexistent_base"
        result = loader._link_to_base(src, base)
        assert base.exists()
        assert result.is_symlink()

    def test_replaces_existing_symlink(self, tmp_path):
        loader = _make_loader(tmp_path)
        src1 = tmp_path / "source1"
        src1.mkdir()
        src2 = tmp_path / "source2"
        src2.mkdir()
        base = tmp_path / "link_base"
        base.mkdir()
        link = base / "source1"
        import os
        os.symlink(src1, link, target_is_directory=True)
        # _safe_link_name will return "source2" for src2 since "source1" exists
        result = loader._link_to_base(src2, base)
        assert result.is_symlink()
        assert result.resolve() == src2.resolve()

    def test_replaces_existing_directory(self, tmp_path):
        loader = _make_loader(tmp_path)
        src = tmp_path / "source_skill"
        src.mkdir()
        base = tmp_path / "link_base"
        base.mkdir()
        existing = base / "source_skill"
        existing.mkdir()
        (existing / "dummy.txt").write_text("data")
        result = loader._link_to_base(src, base)
        assert result.is_symlink()
        assert result.resolve() == src.resolve()


# ---------------------------------------------------------------------------
# _read_skill_name
# ---------------------------------------------------------------------------

class TestReadSkillName:

    def test_delegates_to_parser(self, tmp_path):
        loader = _make_loader(tmp_path)
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text("---\nname: test\n---\nBody")
        loader._claw_parser.read_skill_name.return_value = "parsed-name"
        result = loader._read_skill_name(skill_file)
        assert result == "parsed-name"
        loader._claw_parser.read_skill_name.assert_called_once_with(
            skill_file, ClawSkillLoader.from_markdown
        )


# ---------------------------------------------------------------------------
# _register_skill_root
# ---------------------------------------------------------------------------

class TestRegisterSkillRoot:

    def test_registers_skill_without_link(self, tmp_path):
        loader = _make_loader(tmp_path)
        skill_dir = _create_skill_dir(tmp_path, "my-skill")
        loader._claw_parser.read_skill_name.return_value = "my-skill"
        roots = []
        seen = set()
        record = []
        loader._register_skill_root(
            skill_dir=skill_dir, source="workspace",
            roots=roots, seen=seen, record=record,
        )
        assert "my-skill" in seen
        assert len(roots) == 1
        assert str(skill_dir) in roots
        assert loader._skill_source_by_name["my-skill"] == "workspace"
        assert loader._skill_original_dir_by_name["my-skill"] == skill_dir.resolve()

    def test_registers_skill_with_link(self, tmp_path):
        loader = _make_loader(tmp_path)
        skill_dir = _create_skill_dir(tmp_path, "my-skill")
        loader._claw_parser.read_skill_name.return_value = "my-skill"
        link_base = tmp_path / "links"
        link_base.mkdir()
        roots = []
        seen = set()
        record = []
        loader._register_skill_root(
            skill_dir=skill_dir, source="builtin",
            roots=roots, seen=seen, record=record, link_base=link_base,
        )
        assert "my-skill" in seen
        assert len(roots) == 1
        managed = Path(roots[0])
        assert managed.is_symlink()
        assert managed.resolve() == skill_dir.resolve()

    def test_skips_no_skill_file(self, tmp_path):
        loader = _make_loader(tmp_path)
        empty_dir = tmp_path / "no-skill"
        empty_dir.mkdir()
        roots = []
        seen = set()
        record = []
        loader._register_skill_root(
            skill_dir=empty_dir, source="workspace",
            roots=roots, seen=seen, record=record,
        )
        assert len(roots) == 0

    def test_skips_duplicate_skill(self, tmp_path):
        loader = _make_loader(tmp_path)
        skill_dir = _create_skill_dir(tmp_path, "my-skill")
        loader._claw_parser.read_skill_name.return_value = "my-skill"
        roots = []
        seen = {"my-skill"}
        record = []
        loader._register_skill_root(
            skill_dir=skill_dir, source="workspace",
            roots=roots, seen=seen, record=record,
        )
        assert len(roots) == 0

    def test_uses_dir_name_if_read_name_empty(self, tmp_path):
        loader = _make_loader(tmp_path)
        skill_dir = _create_skill_dir(tmp_path, "fallback-name")
        loader._claw_parser.read_skill_name.return_value = ""
        roots = []
        seen = set()
        record = []
        loader._register_skill_root(
            skill_dir=skill_dir, source="workspace",
            roots=roots, seen=seen, record=record,
        )
        assert "fallback-name" in seen
        assert loader._skill_source_by_name["fallback-name"] == "workspace"

    def test_skips_when_name_and_dir_name_empty(self, tmp_path):
        loader = _make_loader(tmp_path)
        skill_dir = _create_skill_dir(tmp_path, "x")
        loader._claw_parser.read_skill_name.return_value = ""
        roots = []
        seen = set()
        record = []
        # Monkey-patch skill_dir.name to return empty string via a path trick
        # Instead, we test the realistic case where read_skill_name returns None
        loader._claw_parser.read_skill_name.return_value = None
        loader._register_skill_root(
            skill_dir=skill_dir, source="workspace",
            roots=roots, seen=seen, record=record,
        )
        # `None or "x"` → "x", so it should register with dir name "x"
        assert "x" in seen


# ---------------------------------------------------------------------------
# _resolve_skill_roots
# ---------------------------------------------------------------------------

class TestResolveSkillRoots:

    def test_local_directory_root(self, tmp_path):
        loader = _make_loader(tmp_path)
        skill_dir = _create_skill_dir(tmp_path / "local_skills", "my-skill")
        loader._claw_parser.read_skill_name.return_value = "my-skill"
        loader.skills_cfg = SkillRootConfig(
            skill_roots=[],
            builtin_skill_roots=[],
        )

        loader._resolve_skill_roots([str(tmp_path / "local_skills")])

        assert len(loader.local_roots) == 1
        assert loader._skill_source_by_name.get("my-skill") == "workspace"

    def test_empty_and_whitespace_roots_filtered(self, tmp_path):
        loader = _make_loader(tmp_path)
        loader.skills_cfg = SkillRootConfig(
            skill_roots=[],
            builtin_skill_roots=[],
        )
        loader._resolve_skill_roots(["", "  ", "   "])
        assert loader._skill_roots == []
        assert loader.local_roots == []

    def test_file_scheme_directory(self, tmp_path):
        loader = _make_loader(tmp_path)
        skill_root = tmp_path / "file_skills"
        _create_skill_dir(skill_root, "file-skill")
        loader._claw_parser.read_skill_name.return_value = "file-skill"
        loader.skills_cfg = SkillRootConfig(
            skill_roots=[],
            builtin_skill_roots=[],
        )

        loader._resolve_skill_roots([f"file://{skill_root}"])

        assert len(loader.local_file_roots) == 1
        assert loader._skill_source_by_name.get("file-skill") == "workspace"

    def test_file_scheme_archive(self, tmp_path):
        loader = _make_loader(tmp_path)
        archive_path = tmp_path / "skills.zip"
        archive_path.write_text("fake")
        loader._claw_parser.read_skill_name.return_value = "archived-skill"
        loader.skills_cfg = SkillRootConfig(
            skill_roots=[],
            builtin_skill_roots=[],
        )

        with patch("trpc_agent_sdk.server.openclaw.skill._skill_loader.extract_archive") as mock_extract:
            def _fake_extract(src, dest):
                _create_skill_dir(dest, "archived-skill")
            mock_extract.side_effect = _fake_extract
            loader._resolve_skill_roots([f"file://{archive_path}"])

        assert len(loader.local_file_roots) == 1

    def test_file_scheme_nonexistent_path_warns(self, tmp_path):
        loader = _make_loader(tmp_path)
        loader.skills_cfg = SkillRootConfig(
            skill_roots=[],
            builtin_skill_roots=[],
        )
        missing = tmp_path / "does_not_exist"

        with patch("trpc_agent_sdk.server.openclaw.skill._skill_loader.logger") as mock_logger:
            loader._resolve_skill_roots([f"file://{missing}"])
            mock_logger.warning.assert_called()

    def test_file_scheme_archive_extract_failure(self, tmp_path):
        loader = _make_loader(tmp_path)
        archive_path = tmp_path / "bad.zip"
        archive_path.write_text("not-a-real-archive")
        loader.skills_cfg = SkillRootConfig(
            skill_roots=[],
            builtin_skill_roots=[],
        )

        with patch("trpc_agent_sdk.server.openclaw.skill._skill_loader.extract_archive",
                    side_effect=Exception("corrupt")):
            with patch("trpc_agent_sdk.server.openclaw.skill._skill_loader.logger") as mock_logger:
                loader._resolve_skill_roots([f"file://{archive_path}"])
                mock_logger.warning.assert_called()
        assert len(loader.local_file_roots) == 0

    def test_network_root_http(self, tmp_path):
        loader = _make_loader(tmp_path)
        loader._claw_parser.read_skill_name.return_value = "net-skill"
        loader.skills_cfg = SkillRootConfig(
            skill_roots=[],
            builtin_skill_roots=[],
        )

        with patch("trpc_agent_sdk.server.openclaw.skill._skill_loader.download_file") as mock_dl, \
             patch("trpc_agent_sdk.server.openclaw.skill._skill_loader.extract_archive") as mock_extract:
            def _fake_extract(src, dest):
                _create_skill_dir(dest, "net-skill")
            mock_extract.side_effect = _fake_extract
            loader._resolve_skill_roots(["http://example.com/skills.zip"])

        assert len(loader.network_roots) == 1
        mock_dl.assert_called_once()

    def test_network_root_https(self, tmp_path):
        loader = _make_loader(tmp_path)
        loader._claw_parser.read_skill_name.return_value = "net-skill"
        loader.skills_cfg = SkillRootConfig(
            skill_roots=[],
            builtin_skill_roots=[],
        )

        with patch("trpc_agent_sdk.server.openclaw.skill._skill_loader.download_file") as mock_dl, \
             patch("trpc_agent_sdk.server.openclaw.skill._skill_loader.extract_archive") as mock_extract:
            def _fake_extract(src, dest):
                _create_skill_dir(dest, "net-skill")
            mock_extract.side_effect = _fake_extract
            loader._resolve_skill_roots(["https://example.com/skills.tar.gz"])

        assert len(loader.network_roots) == 1
        mock_dl.assert_called_once()

    def test_network_root_download_failure(self, tmp_path):
        loader = _make_loader(tmp_path)
        loader.skills_cfg = SkillRootConfig(
            skill_roots=[],
            builtin_skill_roots=[],
        )

        with patch("trpc_agent_sdk.server.openclaw.skill._skill_loader.download_file",
                    side_effect=Exception("network error")):
            with patch("trpc_agent_sdk.server.openclaw.skill._skill_loader.logger") as mock_logger:
                loader._resolve_skill_roots(["https://example.com/skills.zip"])
                mock_logger.warning.assert_called()
        assert len(loader.network_roots) == 0

    def test_builtin_roots(self, tmp_path):
        loader = _make_loader(tmp_path)
        builtin_dir = tmp_path / "builtin_skills"
        _create_skill_dir(builtin_dir, "builtin-skill")
        loader._claw_parser.read_skill_name.return_value = "builtin-skill"
        loader.skills_cfg = SkillRootConfig(
            skill_roots=[],
            builtin_skill_roots=[str(builtin_dir)],
        )

        loader._resolve_skill_roots([str(builtin_dir)])

        assert len(loader.builtin_roots) == 1
        assert loader._skill_source_by_name.get("builtin-skill") == "builtin"

    def test_builtin_roots_from_config_only(self, tmp_path):
        loader = _make_loader(tmp_path)
        builtin_dir = tmp_path / "builtin_only"
        _create_skill_dir(builtin_dir, "builtin-skill")
        loader._claw_parser.read_skill_name.return_value = "builtin-skill"
        loader.skills_cfg = SkillRootConfig(
            skill_roots=[],
            builtin_skill_roots=[str(builtin_dir)],
        )

        # Not passing builtin_dir in roots - it should still be picked up
        # from builtin_skill_roots config
        loader._resolve_skill_roots([])

        assert len(loader.builtin_roots) == 1

    def test_invalid_scheme_warns(self, tmp_path):
        loader = _make_loader(tmp_path)
        loader.skills_cfg = SkillRootConfig(
            skill_roots=[],
            builtin_skill_roots=[],
        )

        with patch("trpc_agent_sdk.server.openclaw.skill._skill_loader.logger") as mock_logger:
            loader._resolve_skill_roots(["ftp://example.com/skills"])
            mock_logger.warning.assert_called_with("Invalid skill root %s", "ftp://example.com/skills")

    def test_workspace_downloaded_skills_priority(self, tmp_path):
        loader = _make_loader(tmp_path)
        # Create a skill in the downloaded dir
        downloaded_skill = _create_skill_dir(loader._downloaded_skills_root, "shared-skill")
        # And the same skill in a local dir
        local_dir = tmp_path / "local_skills"
        _create_skill_dir(local_dir, "shared-skill")

        loader._claw_parser.read_skill_name.return_value = "shared-skill"
        loader.skills_cfg = SkillRootConfig(
            skill_roots=[],
            builtin_skill_roots=[],
        )

        loader._resolve_skill_roots([str(local_dir)])

        # Downloaded skill should take priority (registered first)
        assert loader._skill_source_by_name.get("shared-skill") == "workspace"
        # Downloaded skill goes into local_roots; local dir duplicate is skipped
        assert len(loader.local_roots) == 1
        assert "downloaded" in loader.local_roots[0]

    def test_builtin_deduplication(self, tmp_path):
        loader = _make_loader(tmp_path)
        builtin_dir = tmp_path / "builtin_skills"
        _create_skill_dir(builtin_dir, "b-skill")
        loader._claw_parser.read_skill_name.return_value = "b-skill"
        loader.skills_cfg = SkillRootConfig(
            skill_roots=[],
            builtin_skill_roots=[str(builtin_dir)],
        )

        # Pass the same builtin path twice in roots
        loader._resolve_skill_roots([str(builtin_dir), str(builtin_dir)])

        assert len(loader.builtin_roots) == 1

    def test_mixed_roots(self, tmp_path):
        loader = _make_loader(tmp_path)

        local_dir = tmp_path / "local"
        _create_skill_dir(local_dir, "local-skill")

        builtin_dir = tmp_path / "builtin"
        _create_skill_dir(builtin_dir, "builtin-skill")

        call_count = {"n": 0}
        def _fake_read_name(skill_file, from_md):
            call_count["n"] += 1
            return skill_file.parent.name

        loader._claw_parser.read_skill_name.side_effect = _fake_read_name
        loader.skills_cfg = SkillRootConfig(
            skill_roots=[],
            builtin_skill_roots=[str(builtin_dir)],
        )

        with patch("trpc_agent_sdk.server.openclaw.skill._skill_loader.download_file") as mock_dl, \
             patch("trpc_agent_sdk.server.openclaw.skill._skill_loader.extract_archive") as mock_extract:
            def _fake_extract(src, dest):
                _create_skill_dir(dest, "net-skill")
            mock_extract.side_effect = _fake_extract

            loader._resolve_skill_roots([
                str(local_dir),
                "https://example.com/net.zip",
                str(builtin_dir),
            ])

        assert "local-skill" in loader._skill_source_by_name
        assert "net-skill" in loader._skill_source_by_name
        assert "builtin-skill" in loader._skill_source_by_name


# ---------------------------------------------------------------------------
# _index
# ---------------------------------------------------------------------------

class TestIndex:

    def test_index_evaluates_and_records_eligible(self, tmp_path):
        loader = _make_loader(
            tmp_path,
            skill_paths={"skill1": str(tmp_path / "s1"), "skill2": str(tmp_path / "s2")},
        )
        loader._claw_parser.parse_metadata.return_value = {"skill_key": "sk1"}
        loader._claw_parser.evaluate_skill_eligibility.return_value = ""

        with patch.object(type(loader).__bases__[0], '_index'):
            with patch.object(loader, '_get_skill_metadata', return_value={"metadata": {"skill_key": "sk1"}}):
                with patch.object(loader, '_read_openclaw_meta',
                                  return_value=({"skill_key": "sk1"}, True)):
                    with patch.object(loader, '_evaluate_skill', return_value=""):
                        with patch.object(loader, '_build_skill_run_env', return_value={"K": "V"}):
                            loader._index()

        assert "skill1" in loader._eligible
        assert "skill2" in loader._eligible
        assert loader._skill_key_map["skill1"] == "sk1"
        assert loader._skill_env_vars["skill1"] == {"K": "V"}

    def test_index_records_ineligible_with_reason(self, tmp_path):
        loader = _make_loader(
            tmp_path,
            skill_paths={"skip-me": str(tmp_path / "s1")},
        )

        def _eval_side_effect(*, name, meta):
            return "missing binary"

        with patch.object(type(loader).__bases__[0], '_index'):
            with patch.object(loader, '_read_openclaw_meta',
                              return_value=({"skill_key": "sk"}, True)):
                with patch.object(loader, '_evaluate_skill', side_effect=_eval_side_effect):
                    loader._index()

        assert "skip-me" not in loader._eligible
        assert loader._reasons["skip-me"] == "missing binary"

    def test_index_uses_name_when_skill_key_empty(self, tmp_path):
        loader = _make_loader(
            tmp_path,
            skill_paths={"my-skill": str(tmp_path / "s1")},
        )

        with patch.object(type(loader).__bases__[0], '_index'):
            with patch.object(loader, '_read_openclaw_meta',
                              return_value=({"skill_key": ""}, False)):
                with patch.object(loader, '_evaluate_skill', return_value=""):
                    with patch.object(loader, '_build_skill_run_env', return_value={}):
                        loader._index()

        assert loader._skill_key_map["my-skill"] == "my-skill"

    def test_index_debug_logs_skip_reason(self, tmp_path):
        loader = _make_loader(
            tmp_path,
            skill_paths={"debug-skill": str(tmp_path / "s1")},
            skills_cfg=SkillRootConfig(debug=True),
        )

        with patch.object(type(loader).__bases__[0], '_index'):
            with patch.object(loader, '_read_openclaw_meta',
                              return_value=({"skill_key": ""}, False)):
                with patch.object(loader, '_evaluate_skill', return_value="some reason"):
                    with patch("trpc_agent_sdk.server.openclaw.skill._skill_loader.logger") as mock_logger:
                        loader._index()
                        mock_logger.info.assert_called()

    def test_index_calls_super(self, tmp_path):
        loader = _make_loader(tmp_path, skill_paths={})
        with patch.object(type(loader).__bases__[0], '_index') as mock_super_index:
            loader._index()
        mock_super_index.assert_called_once()


# ---------------------------------------------------------------------------
# _read_openclaw_meta
# ---------------------------------------------------------------------------

class TestReadOpenclawMeta:

    def test_parses_metadata(self, tmp_path):
        loader = _make_loader(tmp_path)
        loader._claw_parser.parse_metadata.return_value = {"skill_key": "k1", "always": True}
        with patch.object(loader, '_get_skill_metadata',
                          return_value={"metadata": {"skill_key": "k1", "always": True}}):
            meta, has_meta = loader._read_openclaw_meta("my-skill")

        assert has_meta is True
        assert meta["skill_key"] == "k1"

    def test_empty_metadata(self, tmp_path):
        loader = _make_loader(tmp_path)
        loader._claw_parser.parse_metadata.return_value = {}
        with patch.object(loader, '_get_skill_metadata', return_value={}):
            meta, has_meta = loader._read_openclaw_meta("my-skill")

        assert has_meta is False
        assert meta["skill_key"] == ""

    def test_none_metadata(self, tmp_path):
        loader = _make_loader(tmp_path)
        loader._claw_parser.parse_metadata.return_value = {}
        with patch.object(loader, '_get_skill_metadata', return_value=None):
            meta, has_meta = loader._read_openclaw_meta("my-skill")

        assert has_meta is False
        assert isinstance(meta, dict)


# ---------------------------------------------------------------------------
# _evaluate_skill
# ---------------------------------------------------------------------------

class TestEvaluateSkill:

    def test_bundled_skill_source(self, tmp_path):
        bundled = tmp_path / "bundled"
        bundled.mkdir()
        skill_path = bundled / "my-skill"
        skill_path.mkdir()
        loader = _make_loader(
            tmp_path,
            bundled_root=str(bundled),
            skill_paths={"my-skill": str(skill_path)},
        )
        loader._claw_parser.evaluate_skill_eligibility.return_value = ""

        result = loader._evaluate_skill(name="my-skill", meta={})

        loader._claw_parser.evaluate_skill_eligibility.assert_called_once_with(
            skill_name="my-skill",
            source="builtin",
            skill_meta={},
        )
        assert result == ""

    def test_workspace_skill_source(self, tmp_path):
        loader = _make_loader(
            tmp_path,
            bundled_root=str(tmp_path / "bundled"),
            skill_paths={"my-skill": str(tmp_path / "workspace" / "my-skill")},
        )
        loader._claw_parser.evaluate_skill_eligibility.return_value = "missing env"

        result = loader._evaluate_skill(name="my-skill", meta={})

        loader._claw_parser.evaluate_skill_eligibility.assert_called_once_with(
            skill_name="my-skill",
            source="workspace",
            skill_meta={},
        )
        assert result == "missing env"

    def test_none_evaluation_returns_empty(self, tmp_path):
        loader = _make_loader(
            tmp_path,
            skill_paths={"my-skill": str(tmp_path / "s")},
        )
        loader._claw_parser.evaluate_skill_eligibility.return_value = None

        result = loader._evaluate_skill(name="my-skill", meta={})
        assert result == ""


# ---------------------------------------------------------------------------
# summaries
# ---------------------------------------------------------------------------

class TestSummaries:

    def test_filters_by_eligible(self, tmp_path):
        loader = _make_loader(
            tmp_path,
            eligible={"enabled-skill"},
            skill_paths={"enabled-skill": str(tmp_path / "a"), "disabled-skill": str(tmp_path / "b")},
        )
        mock_summaries = [
            MagicMock(name="enabled-skill"),
            MagicMock(name="disabled-skill"),
        ]
        mock_summaries[0].name = "enabled-skill"
        mock_summaries[1].name = "disabled-skill"

        with patch.object(type(loader).__bases__[0], 'summaries', return_value=mock_summaries):
            result = loader.summaries()

        assert len(result) == 1
        assert result[0].name == "enabled-skill"

    def test_empty_eligible_returns_empty(self, tmp_path):
        loader = _make_loader(tmp_path, eligible=set())
        mock_summaries = [MagicMock(name="a")]
        mock_summaries[0].name = "a"

        with patch.object(type(loader).__bases__[0], 'summaries', return_value=mock_summaries):
            result = loader.summaries()

        assert len(result) == 0

    def test_all_eligible(self, tmp_path):
        loader = _make_loader(tmp_path, eligible={"a", "b"})
        mock_summaries = [MagicMock(), MagicMock()]
        mock_summaries[0].name = "a"
        mock_summaries[1].name = "b"

        with patch.object(type(loader).__bases__[0], 'summaries', return_value=mock_summaries):
            result = loader.summaries()

        assert len(result) == 2


# ---------------------------------------------------------------------------
# _resolve_skill_config
# ---------------------------------------------------------------------------

class TestResolveSkillConfig:

    def test_finds_by_skill_key(self, tmp_path):
        cfg = SkillConfig(enabled=True, env={"K": "V"})
        loader = _make_loader(tmp_path, skill_configs={"my-key": cfg})
        result = loader._resolve_skill_config("my-key", "my-name")
        assert result is cfg

    def test_falls_back_to_skill_name(self, tmp_path):
        cfg = SkillConfig(enabled=False)
        loader = _make_loader(tmp_path, skill_configs={"my-name": cfg})
        result = loader._resolve_skill_config("unknown-key", "my-name")
        assert result is cfg

    def test_returns_none_when_not_found(self, tmp_path):
        loader = _make_loader(tmp_path, skill_configs={})
        result = loader._resolve_skill_config("unknown-key", "unknown-name")
        assert result is None

    def test_empty_key_falls_back_to_name(self, tmp_path):
        cfg = SkillConfig(enabled=True)
        loader = _make_loader(tmp_path, skill_configs={"my-name": cfg})
        result = loader._resolve_skill_config("", "my-name")
        assert result is cfg

    def test_empty_key_and_name(self, tmp_path):
        loader = _make_loader(tmp_path, skill_configs={"": SkillConfig()})
        result = loader._resolve_skill_config("", "")
        assert result is None

    def test_key_priority_over_name(self, tmp_path):
        key_cfg = SkillConfig(enabled=True)
        name_cfg = SkillConfig(enabled=False)
        loader = _make_loader(tmp_path, skill_configs={"k": key_cfg, "n": name_cfg})
        result = loader._resolve_skill_config("k", "n")
        assert result is key_cfg


# ---------------------------------------------------------------------------
# _build_skill_run_env
# ---------------------------------------------------------------------------

class TestBuildSkillRunEnv:

    def test_env_from_config(self, tmp_path):
        cfg = SkillConfig(enabled=True, env={"API_KEY": "secret123"})
        loader = _make_loader(tmp_path, skill_configs={"sk1": cfg})
        loader._claw_parser.is_blocked_skill_env_key.return_value = False

        result = loader._build_skill_run_env("my-skill", "sk1", {})
        assert result == {"API_KEY": "secret123"}

    def test_blocked_env_key_skipped(self, tmp_path):
        cfg = SkillConfig(enabled=True, env={"BLOCKED": "val", "ALLOWED": "val2"})
        loader = _make_loader(tmp_path, skill_configs={"sk1": cfg})
        loader._claw_parser.is_blocked_skill_env_key.side_effect = lambda k: k == "BLOCKED"

        result = loader._build_skill_run_env("my-skill", "sk1", {})
        assert "BLOCKED" not in result
        assert result["ALLOWED"] == "val2"

    def test_empty_env_keys_skipped(self, tmp_path):
        cfg = SkillConfig(enabled=True, env={"": "val", "KEY": ""})
        loader = _make_loader(tmp_path, skill_configs={"sk1": cfg})
        loader._claw_parser.is_blocked_skill_env_key.return_value = False

        result = loader._build_skill_run_env("my-skill", "sk1", {})
        assert result == {}

    def test_fallback_to_host_env(self, tmp_path):
        loader = _make_loader(tmp_path, skill_configs={})
        loader._claw_parser.is_blocked_skill_env_key.return_value = False

        meta = {"requires": {"env": ["MY_HOST_VAR"]}}
        with patch.dict("os.environ", {"MY_HOST_VAR": "host_value"}):
            result = loader._build_skill_run_env("my-skill", "sk1", meta)
        assert result == {"MY_HOST_VAR": "host_value"}

    def test_config_env_overrides_host_env(self, tmp_path):
        cfg = SkillConfig(enabled=True, env={"MY_VAR": "from_config"})
        loader = _make_loader(tmp_path, skill_configs={"sk1": cfg})
        loader._claw_parser.is_blocked_skill_env_key.return_value = False

        meta = {"requires": {"env": ["MY_VAR"]}}
        with patch.dict("os.environ", {"MY_VAR": "from_host"}):
            result = loader._build_skill_run_env("my-skill", "sk1", meta)
        assert result["MY_VAR"] == "from_config"

    def test_no_config_no_requires(self, tmp_path):
        loader = _make_loader(tmp_path, skill_configs={})
        result = loader._build_skill_run_env("my-skill", "sk1", {})
        assert result == {}

    def test_requires_env_not_list(self, tmp_path):
        loader = _make_loader(tmp_path, skill_configs={})
        meta = {"requires": {"env": "not-a-list"}}
        result = loader._build_skill_run_env("my-skill", "sk1", meta)
        assert result == {}

    def test_requires_not_dict(self, tmp_path):
        loader = _make_loader(tmp_path, skill_configs={})
        meta = {"requires": "invalid"}
        result = loader._build_skill_run_env("my-skill", "sk1", meta)
        assert result == {}

    def test_host_env_empty_value_skipped(self, tmp_path):
        loader = _make_loader(tmp_path, skill_configs={})
        loader._claw_parser.is_blocked_skill_env_key.return_value = False

        meta = {"requires": {"env": ["EMPTY_VAR"]}}
        with patch.dict("os.environ", {"EMPTY_VAR": ""}, clear=False):
            result = loader._build_skill_run_env("my-skill", "sk1", meta)
        assert "EMPTY_VAR" not in result

    def test_host_env_missing_key_skipped(self, tmp_path):
        loader = _make_loader(tmp_path, skill_configs={})
        loader._claw_parser.is_blocked_skill_env_key.return_value = False

        meta = {"requires": {"env": ["MISSING_KEY"]}}
        with patch.dict("os.environ", {}, clear=True):
            result = loader._build_skill_run_env("my-skill", "sk1", meta)
        assert "MISSING_KEY" not in result

    def test_blocked_host_env_key_skipped(self, tmp_path):
        loader = _make_loader(tmp_path, skill_configs={})
        loader._claw_parser.is_blocked_skill_env_key.side_effect = lambda k: k == "SECRET"

        meta = {"requires": {"env": ["SECRET"]}}
        with patch.dict("os.environ", {"SECRET": "val"}):
            result = loader._build_skill_run_env("my-skill", "sk1", meta)
        assert "SECRET" not in result


# ---------------------------------------------------------------------------
# refresh
# ---------------------------------------------------------------------------

class TestRefresh:

    def test_calls_super_refresh(self, tmp_path):
        loader = _make_loader(tmp_path)
        with patch.object(type(loader).__bases__[0], 'refresh') as mock_refresh:
            loader.refresh()
        mock_refresh.assert_called_once()


# ---------------------------------------------------------------------------
# _get_skill_metadata
# ---------------------------------------------------------------------------

class TestGetSkillMetadata:

    def test_reads_and_parses_skill_md(self, tmp_path):
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: test\nmetadata:\n  skill_key: k1\n---\nBody content"
        )
        loader = _make_loader(tmp_path, skill_paths={"my-skill": str(skill_dir)})

        result = loader._get_skill_metadata("my-skill")

        assert result is not None
        assert "name" in result

    def test_returns_none_on_missing_file(self, tmp_path):
        loader = _make_loader(
            tmp_path,
            skill_paths={"my-skill": str(tmp_path / "nonexistent")},
        )
        result = loader._get_skill_metadata("my-skill")
        assert result is None

    def test_returns_none_on_missing_key(self, tmp_path):
        loader = _make_loader(tmp_path, skill_paths={})
        result = loader._get_skill_metadata("nonexistent")
        assert result is None

    def test_returns_none_on_invalid_content(self, tmp_path):
        skill_dir = tmp_path / "bad-skill"
        skill_dir.mkdir()
        # Write binary-like invalid UTF-8 content
        (skill_dir / "SKILL.md").write_bytes(b"\x80\x81\x82")
        loader = _make_loader(tmp_path, skill_paths={"bad-skill": str(skill_dir)})

        result = loader._get_skill_metadata("bad-skill")
        assert result is None

    def test_parses_front_matter_correctly(self, tmp_path):
        skill_dir = tmp_path / "fm-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: A test skill\n---\n# Body"
        )
        loader = _make_loader(tmp_path, skill_paths={"fm-skill": str(skill_dir)})

        result = loader._get_skill_metadata("fm-skill")
        assert result is not None
        assert result.get("name") == "my-skill"
        assert result.get("description") == "A test skill"

    def test_no_front_matter_returns_empty_dict(self, tmp_path):
        skill_dir = tmp_path / "no-fm"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("# Just a body\nNo front matter here.")
        loader = _make_loader(tmp_path, skill_paths={"no-fm": str(skill_dir)})

        result = loader._get_skill_metadata("no-fm")
        assert result is not None
        assert isinstance(result, dict)
