"""Unit tests for trpc_agent_sdk.server.openclaw.skill._deps."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from trpc_agent_sdk.server.openclaw.skill._deps import (
    _command_for_action,
    _detect_package_manager,
    _ensure_workspace_layout,
    _has_config_key,
    _inspect_source,
    _merge_sources,
    _normalize_any_bins,
    _normalize_list,
    _normalize_skill_names,
    _pick_install_command,
    _resolve_profiles,
    _sources_for_profiles,
    _split_csv,
    apply_dependency_plan,
    inspect_skill_dependencies,
    render_dependency_report,
    report_to_json,
    write_test_config_file,
)


# ---------------------------------------------------------------------------
# _split_csv
# ---------------------------------------------------------------------------

class TestSplitCsv:

    def test_empty_string(self):
        assert _split_csv("") == []

    def test_single_item(self):
        assert _split_csv("foo") == ["foo"]

    def test_multiple_items(self):
        assert _split_csv("foo,bar,baz") == ["foo", "bar", "baz"]

    def test_strips_whitespace(self):
        assert _split_csv("  foo , bar , baz  ") == ["foo", "bar", "baz"]

    def test_deduplicates(self):
        assert _split_csv("foo,bar,foo") == ["foo", "bar"]

    def test_skips_empty_parts(self):
        assert _split_csv("foo,,bar,") == ["foo", "bar"]

    def test_none_input(self):
        assert _split_csv(None) == []


# ---------------------------------------------------------------------------
# _command_for_action
# ---------------------------------------------------------------------------

class TestCommandForAction:

    def test_brew(self):
        assert _command_for_action({"kind": "brew", "package": "git"}) == "brew install git"

    def test_apt(self):
        assert _command_for_action({"kind": "apt", "package": "git"}) == "apt-get install -y git"

    def test_dnf(self):
        assert _command_for_action({"kind": "dnf", "package": "git"}) == "dnf install -y git"

    def test_yum(self):
        assert _command_for_action({"kind": "yum", "package": "git"}) == "yum install -y git"

    def test_pip(self):
        assert _command_for_action({"kind": "pip", "package": "requests"}) == "python -m pip install requests"

    def test_uv(self):
        assert _command_for_action({"kind": "uv", "package": "requests"}) == "uv pip install requests"

    def test_empty_kind(self):
        assert _command_for_action({"kind": "", "package": "git"}) == ""

    def test_empty_package(self):
        assert _command_for_action({"kind": "brew", "package": ""}) == ""

    def test_empty_dict(self):
        assert _command_for_action({}) == ""

    def test_unknown_kind(self):
        assert _command_for_action({"kind": "unknown", "package": "pkg"}) == ""


# ---------------------------------------------------------------------------
# _pick_install_command
# ---------------------------------------------------------------------------

class TestPickInstallCommand:

    @patch("trpc_agent_sdk.server.openclaw.skill._deps._detect_package_manager", return_value="brew")
    def test_with_matching_pkg_manager(self, _mock):
        actions = [
            {"kind": "apt", "package": "poppler-utils"},
            {"kind": "brew", "package": "poppler"},
        ]
        assert _pick_install_command(actions) == "brew install poppler"

    @patch("trpc_agent_sdk.server.openclaw.skill._deps._detect_package_manager", return_value="")
    def test_without_detected_pkg_manager(self, _mock):
        actions = [{"kind": "brew", "package": "poppler"}]
        assert _pick_install_command(actions) == "brew install poppler"

    def test_empty_actions(self):
        assert _pick_install_command([]) == ""

    @patch("trpc_agent_sdk.server.openclaw.skill._deps._detect_package_manager", return_value="dnf")
    def test_no_matching_kind_falls_through(self, _mock):
        actions = [{"kind": "brew", "package": "pkg"}]
        assert _pick_install_command(actions) == "brew install pkg"


# ---------------------------------------------------------------------------
# _resolve_profiles
# ---------------------------------------------------------------------------

class TestResolveProfiles:

    def test_single_profile(self):
        result = _resolve_profiles(["pdf"])
        assert "pdf" in result

    def test_profile_with_expansion(self):
        result = _resolve_profiles(["common-file-tools"])
        assert "pdf" in result
        assert "audio" in result

    def test_unknown_profile_raises(self):
        with pytest.raises(ValueError, match="unknown dependency profile"):
            _resolve_profiles(["nonexistent"])

    def test_empty_defaults(self):
        result = _resolve_profiles([])
        assert len(result) > 0

    def test_deduplicates(self):
        result = _resolve_profiles(["pdf", "pdf"])
        assert result.count("pdf") == 1

    def test_aggregate_profile_not_in_output(self):
        result = _resolve_profiles(["common-file-tools"])
        assert "common-file-tools" not in result


# ---------------------------------------------------------------------------
# _normalize_any_bins
# ---------------------------------------------------------------------------

class TestNormalizeAnyBins:

    def test_flat_list_of_strings(self):
        result = _normalize_any_bins(["magick", "convert"])
        assert result == [["magick", "convert"]]

    def test_nested_lists(self):
        result = _normalize_any_bins([["magick", "convert"], ["ffmpeg"]])
        assert result == [["magick", "convert"], ["ffmpeg"]]

    def test_non_list(self):
        assert _normalize_any_bins("string") == []

    def test_empty_list(self):
        assert _normalize_any_bins([]) == []

    def test_strips_whitespace(self):
        result = _normalize_any_bins(["  magick  ", "  convert  "])
        assert result == [["magick", "convert"]]

    def test_filters_empty_strings(self):
        result = _normalize_any_bins(["magick", "", "  "])
        assert result == [["magick"]]

    def test_none_input(self):
        assert _normalize_any_bins(None) == []


# ---------------------------------------------------------------------------
# _normalize_list
# ---------------------------------------------------------------------------

class TestNormalizeList:

    def test_list_of_strings(self):
        assert _normalize_list(["a", "b", "c"]) == ["a", "b", "c"]

    def test_non_list_returns_empty(self):
        assert _normalize_list("string") == []

    def test_empty_list(self):
        assert _normalize_list([]) == []

    def test_strips_whitespace(self):
        assert _normalize_list(["  a  ", "b"]) == ["a", "b"]

    def test_filters_empty_items(self):
        assert _normalize_list(["a", "", "  ", "b"]) == ["a", "b"]


# ---------------------------------------------------------------------------
# _has_config_key
# ---------------------------------------------------------------------------

class TestHasConfigKey:

    def test_exact_match(self):
        assert _has_config_key({"foo", "bar"}, "foo") is True

    def test_prefix_match(self):
        assert _has_config_key({"foo.bar.baz"}, "foo.bar") is True

    def test_not_found(self):
        assert _has_config_key({"foo", "bar"}, "baz") is False

    def test_empty_set(self):
        assert _has_config_key(set(), "foo") is False

    def test_partial_name_no_dot(self):
        assert _has_config_key({"foobar"}, "foo") is False


# ---------------------------------------------------------------------------
# _inspect_source
# ---------------------------------------------------------------------------

class TestInspectSource:

    @patch("trpc_agent_sdk.server.openclaw.skill._deps.shutil.which", return_value="/usr/bin/git")
    def test_all_present(self, _mock):
        source = {
            "name": "test",
            "description": "Test",
            "requires": {"bins": ["git"]},
            "install": "",
        }
        result = _inspect_source(source, set())
        assert result["ok"] is True
        assert result["missing"]["bins"] == []

    @patch("trpc_agent_sdk.server.openclaw.skill._deps.shutil.which", return_value=None)
    def test_missing_bins(self, _mock):
        source = {
            "name": "test",
            "requires": {"bins": ["nonexistent"]},
            "install": "brew install something",
        }
        result = _inspect_source(source, set())
        assert result["ok"] is False
        assert "nonexistent" in result["missing"]["bins"]

    @patch("trpc_agent_sdk.server.openclaw.skill._deps.shutil.which", return_value="/usr/bin/git")
    def test_missing_env(self, _mock):
        source = {
            "name": "test",
            "requires": {"env": ["MISSING_ENV_VAR_XYZ"]},
            "install": "",
        }
        with patch.dict("os.environ", {}, clear=True):
            result = _inspect_source(source, set())
        assert result["ok"] is False
        assert "MISSING_ENV_VAR_XYZ" in result["missing"]["env"]

    @patch("trpc_agent_sdk.server.openclaw.skill._deps.shutil.which", return_value="/usr/bin/git")
    def test_missing_config(self, _mock):
        source = {
            "name": "test",
            "requires": {"config": ["my.key"]},
            "install": "",
        }
        result = _inspect_source(source, set())
        assert result["ok"] is False
        assert "my.key" in result["missing"]["config"]

    @patch("trpc_agent_sdk.server.openclaw.skill._deps.shutil.which", return_value="/usr/bin/git")
    def test_install_as_list(self, _mock):
        source = {
            "name": "test",
            "requires": {},
            "install": [{"kind": "brew", "package": "pkg"}],
        }
        with patch("trpc_agent_sdk.server.openclaw.skill._deps._pick_install_command",
                    return_value="brew install pkg"):
            result = _inspect_source(source, set())
        assert result["install"] == "brew install pkg"

    @patch("trpc_agent_sdk.server.openclaw.skill._deps.shutil.which", return_value="/usr/bin/git")
    def test_install_as_string(self, _mock):
        source = {
            "name": "test",
            "requires": {},
            "install": "pip install something",
        }
        result = _inspect_source(source, set())
        assert result["install"] == "pip install something"

    @patch("trpc_agent_sdk.server.openclaw.skill._deps.shutil.which", return_value=None)
    def test_missing_any_bins(self, _mock):
        source = {
            "name": "test",
            "requires": {"any_bins": ["magick", "convert"]},
            "install": "",
        }
        result = _inspect_source(source, set())
        assert result["ok"] is False
        assert len(result["missing"]["any_bins"]) > 0


# ---------------------------------------------------------------------------
# apply_dependency_plan
# ---------------------------------------------------------------------------

class TestApplyDependencyPlan:

    def test_empty_plan(self):
        result = apply_dependency_plan({"plan": []})
        assert result["steps"] == []
        assert result["has_failures"] is False

    @patch("trpc_agent_sdk.server.openclaw.skill._deps.subprocess.run")
    def test_successful_step(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        report = {"plan": [{"skill": "test", "command": "echo hello"}]}
        result = apply_dependency_plan(report)
        assert result["steps"][0]["status"] == "applied"
        assert result["has_failures"] is False

    @patch("trpc_agent_sdk.server.openclaw.skill._deps.subprocess.run")
    def test_failed_step(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        report = {"plan": [{"skill": "test", "command": "false"}]}
        result = apply_dependency_plan(report)
        assert result["steps"][0]["status"] == "failed"
        assert result["has_failures"] is True

    def test_deferred_step(self):
        report = {"plan": [{"skill": "test", "command": ""}]}
        result = apply_dependency_plan(report)
        assert result["steps"][0]["status"] == "deferred"
        assert result["deferred"][0]["skill"] == "test"

    @patch("trpc_agent_sdk.server.openclaw.skill._deps.subprocess.run")
    def test_fail_fast(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        report = {
            "plan": [
                {"skill": "first", "command": "cmd1"},
                {"skill": "second", "command": "cmd2"},
            ]
        }
        result = apply_dependency_plan(report, continue_on_error=False)
        assert len(result["steps"]) == 1
        assert result["steps"][0]["skill"] == "first"

    @patch("trpc_agent_sdk.server.openclaw.skill._deps.subprocess.run")
    def test_continue_on_error(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        report = {
            "plan": [
                {"skill": "first", "command": "cmd1"},
                {"skill": "second", "command": "cmd2"},
            ]
        }
        result = apply_dependency_plan(report, continue_on_error=True)
        assert len(result["steps"]) == 2

    def test_no_plan_key(self):
        result = apply_dependency_plan({})
        assert result["steps"] == []


# ---------------------------------------------------------------------------
# render_dependency_report
# ---------------------------------------------------------------------------

class TestRenderDependencyReport:

    def test_with_sources(self):
        report = {
            "selected_profiles": ["pdf"],
            "selected": [],
            "sources": [
                {
                    "name": "pdf",
                    "ok": True,
                    "missing": {"bins": [], "any_bins": [], "env": [], "config": []},
                }
            ],
            "missing": {"bins": [], "any_bins": [], "env": [], "config": []},
            "plan": [],
        }
        output = render_dependency_report(report)
        assert "pdf" in output
        assert "ok" in output

    def test_with_plan(self):
        report = {
            "selected_profiles": [],
            "selected": ["my-skill"],
            "sources": [],
            "missing": {"bins": ["git"], "any_bins": [], "env": [], "config": []},
            "plan": [{"skill": "my-skill", "command": "brew install git"}],
        }
        output = render_dependency_report(report)
        assert "brew install git" in output
        assert "Install plan:" in output

    def test_with_apply_result(self):
        report = {
            "selected_profiles": [],
            "selected": [],
            "sources": [],
            "missing": {"bins": [], "any_bins": [], "env": [], "config": []},
            "plan": [],
            "apply_result": {
                "steps": [{
                    "status": "applied",
                    "skill": "test",
                    "command": "echo ok",
                    "exit_code": 0,
                    "stderr": "",
                }]
            },
        }
        output = render_dependency_report(report)
        assert "Apply result:" in output
        assert "applied" in output

    def test_empty_plan_shows_none(self):
        report = {
            "selected_profiles": [],
            "selected": [],
            "sources": [],
            "missing": {"bins": [], "any_bins": [], "env": [], "config": []},
            "plan": [],
        }
        output = render_dependency_report(report)
        assert "Install plan: none" in output

    def test_missing_bins_shown_in_source(self):
        report = {
            "selected_profiles": [],
            "selected": [],
            "sources": [{
                "name": "pdf",
                "ok": False,
                "missing": {"bins": ["pdftotext"], "any_bins": [], "env": [], "config": []},
            }],
            "missing": {"bins": ["pdftotext"], "any_bins": [], "env": [], "config": []},
            "plan": [],
        }
        output = render_dependency_report(report)
        assert "pdftotext" in output


# ---------------------------------------------------------------------------
# report_to_json
# ---------------------------------------------------------------------------

class TestReportToJson:

    def test_returns_valid_json(self):
        report = {"key": "value", "list": [1, 2, 3]}
        output = report_to_json(report)
        parsed = json.loads(output)
        assert parsed == report

    def test_preserves_unicode(self):
        report = {"name": "中文测试"}
        output = report_to_json(report)
        assert "中文测试" in output


# ---------------------------------------------------------------------------
# _merge_sources
# ---------------------------------------------------------------------------

class TestMergeSources:

    def test_sorts_by_name(self):
        sources = [
            {"name": "zzz", "requires": {}},
            {"name": "aaa", "requires": {}},
        ]
        result = _merge_sources(sources)
        assert result[0]["name"] == "aaa"
        assert result[1]["name"] == "zzz"

    def test_skips_empty_names(self):
        sources = [
            {"name": "", "requires": {}},
            {"name": "valid", "requires": {}},
        ]
        result = _merge_sources(sources)
        assert len(result) == 1
        assert result[0]["name"] == "valid"

    def test_strips_whitespace(self):
        sources = [{"name": "  foo  ", "requires": {}}]
        result = _merge_sources(sources)
        assert result[0]["name"] == "foo"

    def test_empty_input(self):
        assert _merge_sources([]) == []


# ---------------------------------------------------------------------------
# _ensure_workspace_layout
# ---------------------------------------------------------------------------

class TestEnsureWorkspaceLayout:

    def test_creates_root_and_subdirs(self, tmp_path):
        ws = tmp_path / "workspace"
        _ensure_workspace_layout(ws)
        assert ws.is_dir()
        for sub in ("sessions", "soul", "user", "tool", "agent", "memory", "skills"):
            assert (ws / sub).is_dir()

    def test_idempotent(self, tmp_path):
        ws = tmp_path / "workspace"
        _ensure_workspace_layout(ws)
        _ensure_workspace_layout(ws)
        assert ws.is_dir()

    def test_nested_path(self, tmp_path):
        ws = tmp_path / "a" / "b" / "c"
        _ensure_workspace_layout(ws)
        assert ws.is_dir()
        assert (ws / "skills").is_dir()


# ---------------------------------------------------------------------------
# write_test_config_file
# ---------------------------------------------------------------------------

class TestWriteTestConfigFile:

    def test_creates_config_json(self, tmp_path):
        cfg = tmp_path / "config.json"
        ws = tmp_path / "ws"
        result = write_test_config_file(cfg, ws)
        assert result == cfg.resolve()
        data = json.loads(cfg.read_text(encoding="utf-8"))
        assert "agent" in data
        assert data["agent"]["workspace"] == str(ws.resolve())

    def test_creates_parent_dirs(self, tmp_path):
        cfg = tmp_path / "nested" / "deep" / "config.json"
        ws = tmp_path / "ws"
        write_test_config_file(cfg, ws)
        assert cfg.exists()

    def test_overwrites_existing(self, tmp_path):
        cfg = tmp_path / "config.json"
        ws1 = tmp_path / "ws1"
        ws2 = tmp_path / "ws2"
        write_test_config_file(cfg, ws1)
        write_test_config_file(cfg, ws2)
        data = json.loads(cfg.read_text(encoding="utf-8"))
        assert data["agent"]["workspace"] == str(ws2.resolve())

    def test_returns_resolved_path(self, tmp_path):
        cfg = tmp_path / "config.json"
        ws = tmp_path / "ws"
        result = write_test_config_file(cfg, ws)
        assert result.is_absolute()


# ---------------------------------------------------------------------------
# _normalize_skill_names
# ---------------------------------------------------------------------------

class TestNormalizeSkillNames:

    def test_delegates_to_split_csv(self):
        assert _normalize_skill_names("a,b,c") == ["a", "b", "c"]

    def test_empty_string(self):
        assert _normalize_skill_names("") == []

    def test_deduplicates(self):
        assert _normalize_skill_names("x,x,y") == ["x", "y"]


# ---------------------------------------------------------------------------
# _detect_package_manager
# ---------------------------------------------------------------------------

class TestDetectPackageManager:

    @patch("trpc_agent_sdk.server.openclaw.skill._deps.shutil.which", return_value="/usr/local/bin/brew")
    def test_detects_brew(self, _mock):
        assert _detect_package_manager() == "brew"

    @patch("trpc_agent_sdk.server.openclaw.skill._deps.shutil.which")
    def test_detects_apt_get_returns_apt(self, mock_which):
        def side_effect(name):
            return "/usr/bin/apt-get" if name == "apt-get" else None
        mock_which.side_effect = side_effect
        assert _detect_package_manager() == "apt"

    @patch("trpc_agent_sdk.server.openclaw.skill._deps.shutil.which")
    def test_detects_dnf(self, mock_which):
        def side_effect(name):
            return "/usr/bin/dnf" if name == "dnf" else None
        mock_which.side_effect = side_effect
        assert _detect_package_manager() == "dnf"

    @patch("trpc_agent_sdk.server.openclaw.skill._deps.shutil.which")
    def test_detects_yum(self, mock_which):
        def side_effect(name):
            return "/usr/bin/yum" if name == "yum" else None
        mock_which.side_effect = side_effect
        assert _detect_package_manager() == "yum"

    @patch("trpc_agent_sdk.server.openclaw.skill._deps.shutil.which", return_value=None)
    def test_none_found_returns_empty(self, _mock):
        assert _detect_package_manager() == ""


# ---------------------------------------------------------------------------
# _sources_for_profiles
# ---------------------------------------------------------------------------

class TestSourcesForProfiles:

    def test_single_profile(self):
        result = _sources_for_profiles(["pdf"])
        assert len(result) == 1
        assert result[0]["name"] == "pdf"
        assert "description" in result[0]
        assert "requires" in result[0]
        assert "install" in result[0]

    def test_aggregate_profile_expands(self):
        result = _sources_for_profiles(["common-file-tools"])
        names = [item["name"] for item in result]
        assert "pdf" in names
        assert "audio" in names
        assert "common-file-tools" not in names

    def test_empty_profile_uses_defaults(self):
        result = _sources_for_profiles([])
        assert len(result) > 0

    def test_profile_with_no_install_actions(self):
        result = _sources_for_profiles(["office"])
        assert len(result) == 1
        assert result[0]["install"] == ""


# ---------------------------------------------------------------------------
# _pick_install_command – extra branch for line 272
# ---------------------------------------------------------------------------

class TestPickInstallCommandExtraBranches:

    @patch("trpc_agent_sdk.server.openclaw.skill._deps._detect_package_manager", return_value="brew")
    def test_all_actions_produce_empty_commands(self, _mock):
        actions = [{"kind": "unknown", "package": "pkg"}, {"kind": "", "package": ""}]
        assert _pick_install_command(actions) == ""


# ---------------------------------------------------------------------------
# inspect_skill_dependencies
# ---------------------------------------------------------------------------

class TestInspectSkillDependencies:

    @patch("trpc_agent_sdk.server.openclaw.skill._deps.ClawSkillLoader")
    @patch("trpc_agent_sdk.server.openclaw.skill._deps.load_config")
    def test_by_profiles(self, mock_load_config, mock_loader_cls, tmp_path):
        mock_config = MagicMock()
        mock_config.agent.workspace = str(tmp_path)
        mock_config.skills = MagicMock()
        mock_config.skills.config_keys = []
        mock_config.skills.skill_roots = []
        mock_config.skills.builtin_skill_roots = []
        mock_load_config.return_value = mock_config
        mock_loader = MagicMock()
        mock_loader.dependency_sources.return_value = []
        mock_loader_cls.return_value = mock_loader

        result = inspect_skill_dependencies(
            config_path=tmp_path / "config.json",
            workspace=tmp_path / "ws",
            profiles_raw="pdf",
        )
        assert "sources" in result
        assert result["profile_sources_supported"] is True
        assert result["selected_profiles"] == ["pdf"]

    @patch("trpc_agent_sdk.server.openclaw.skill._deps.ClawSkillLoader")
    @patch("trpc_agent_sdk.server.openclaw.skill._deps.load_config")
    def test_by_skills(self, mock_load_config, mock_loader_cls, tmp_path):
        mock_config = MagicMock()
        mock_config.agent.workspace = str(tmp_path)
        mock_config.skills = MagicMock()
        mock_config.skills.config_keys = []
        mock_config.skills.skill_roots = []
        mock_config.skills.builtin_skill_roots = []
        mock_load_config.return_value = mock_config

        mock_loader = MagicMock()
        mock_loader.dependency_sources.return_value = [{
            "name": "my-skill",
            "description": "A skill",
            "requires": {},
            "install": "",
        }]
        mock_loader_cls.return_value = mock_loader

        result = inspect_skill_dependencies(
            config_path=tmp_path / "config.json",
            workspace=tmp_path / "ws",
            skills_raw="my-skill",
        )
        assert result["selected"] == ["my-skill"]
        names = [s["name"] for s in result["sources"]]
        assert "my-skill" in names

    @patch("trpc_agent_sdk.server.openclaw.skill._deps.ClawSkillLoader")
    @patch("trpc_agent_sdk.server.openclaw.skill._deps.load_config")
    def test_unknown_skill_raises(self, mock_load_config, mock_loader_cls, tmp_path):
        mock_config = MagicMock()
        mock_config.agent.workspace = str(tmp_path)
        mock_config.skills = MagicMock()
        mock_config.skills.config_keys = []
        mock_config.skills.skill_roots = []
        mock_load_config.return_value = mock_config

        mock_loader = MagicMock()
        mock_loader.dependency_sources.return_value = []
        mock_loader_cls.return_value = mock_loader

        with pytest.raises(ValueError, match="unknown skill"):
            inspect_skill_dependencies(
                config_path=tmp_path / "config.json",
                workspace=tmp_path / "ws",
                skills_raw="nonexistent",
            )

    @patch("trpc_agent_sdk.server.openclaw.skill._deps.ClawSkillLoader")
    @patch("trpc_agent_sdk.server.openclaw.skill._deps.load_config")
    def test_defaults_to_default_profiles_when_nothing_given(self, mock_load_config, mock_loader_cls, tmp_path):
        mock_config = MagicMock()
        mock_config.agent.workspace = str(tmp_path)
        mock_config.skills = MagicMock()
        mock_config.skills.config_keys = []
        mock_config.skills.skill_roots = []
        mock_load_config.return_value = mock_config
        mock_loader = MagicMock()
        mock_loader.dependency_sources.return_value = []
        mock_loader_cls.return_value = mock_loader

        result = inspect_skill_dependencies(
            config_path=tmp_path / "config.json",
            workspace=tmp_path / "ws",
        )
        assert len(result["selected_profiles"]) > 0

    @patch("trpc_agent_sdk.server.openclaw.skill._deps.ClawSkillLoader")
    @patch("trpc_agent_sdk.server.openclaw.skill._deps.load_config")
    def test_without_workspace(self, mock_load_config, mock_loader_cls, tmp_path):
        mock_config = MagicMock()
        mock_config.agent.workspace = str(tmp_path)
        mock_config.skills = MagicMock()
        mock_config.skills.config_keys = []
        mock_config.skills.skill_roots = []
        mock_load_config.return_value = mock_config
        mock_loader = MagicMock()
        mock_loader.dependency_sources.return_value = []
        mock_loader_cls.return_value = mock_loader

        result = inspect_skill_dependencies(
            config_path=tmp_path / "config.json",
            profiles_raw="pdf",
        )
        assert result["profile_sources_supported"] is True

    @patch("trpc_agent_sdk.server.openclaw.skill._deps.ClawSkillLoader")
    @patch("trpc_agent_sdk.server.openclaw.skill._deps.load_config")
    def test_skills_root_override(self, mock_load_config, mock_loader_cls, tmp_path):
        mock_config = MagicMock()
        mock_config.agent.workspace = str(tmp_path)
        mock_config.skills = MagicMock()
        mock_config.skills.config_keys = []
        mock_config.skills.skill_roots = []
        mock_load_config.return_value = mock_config
        mock_loader = MagicMock()
        mock_loader.dependency_sources.return_value = []
        mock_loader_cls.return_value = mock_loader

        inspect_skill_dependencies(
            config_path=tmp_path / "config.json",
            workspace=tmp_path / "ws",
            profiles_raw="pdf",
            skills_root="/custom/root",
            skills_extra_dirs_raw="/extra/a,/extra/b",
            skills_allow_bundled_raw="some.skill",
        )
        assert mock_config.skills.skill_roots == ["/custom/root", "/extra/a", "/extra/b"]
        assert mock_config.skills.allow_bundled == ["some.skill"]

    @patch("trpc_agent_sdk.server.openclaw.skill._deps.ClawSkillLoader")
    @patch("trpc_agent_sdk.server.openclaw.skill._deps.load_config")
    def test_has_missing_flag(self, mock_load_config, mock_loader_cls, tmp_path):
        mock_config = MagicMock()
        mock_config.agent.workspace = str(tmp_path)
        mock_config.skills = MagicMock()
        mock_config.skills.config_keys = []
        mock_config.skills.skill_roots = []
        mock_load_config.return_value = mock_config
        mock_loader = MagicMock()
        mock_loader.dependency_sources.return_value = []
        mock_loader_cls.return_value = mock_loader

        result = inspect_skill_dependencies(
            config_path=tmp_path / "config.json",
            workspace=tmp_path / "ws",
            profiles_raw="office",
        )
        assert "has_missing" in result

    @patch("trpc_agent_sdk.server.openclaw.skill._deps.ClawSkillLoader")
    @patch("trpc_agent_sdk.server.openclaw.skill._deps.load_config")
    def test_state_dir_passthrough(self, mock_load_config, mock_loader_cls, tmp_path):
        mock_config = MagicMock()
        mock_config.agent.workspace = str(tmp_path)
        mock_config.skills = MagicMock()
        mock_config.skills.config_keys = []
        mock_config.skills.skill_roots = []
        mock_load_config.return_value = mock_config
        mock_loader = MagicMock()
        mock_loader.dependency_sources.return_value = []
        mock_loader_cls.return_value = mock_loader

        result = inspect_skill_dependencies(
            config_path=tmp_path / "config.json",
            workspace=tmp_path / "ws",
            profiles_raw="pdf",
            state_dir="/some/dir",
        )
        assert result["state_dir"] == "/some/dir"


# ---------------------------------------------------------------------------
# render_dependency_report – additional branches
# ---------------------------------------------------------------------------

class TestRenderDependencyReportExtraBranches:

    def test_source_missing_any_bins(self):
        report = {
            "selected_profiles": [],
            "selected": [],
            "sources": [{
                "name": "image",
                "ok": False,
                "missing": {
                    "bins": [],
                    "any_bins": [["magick", "convert"]],
                    "env": [],
                    "config": [],
                },
            }],
            "missing": {"bins": [], "any_bins": [["magick", "convert"]], "env": [], "config": []},
            "plan": [],
        }
        output = render_dependency_report(report)
        assert "magick/convert" in output

    def test_source_missing_env(self):
        report = {
            "selected_profiles": [],
            "selected": [],
            "sources": [{
                "name": "test",
                "ok": False,
                "missing": {
                    "bins": [],
                    "any_bins": [],
                    "env": ["MY_API_KEY"],
                    "config": [],
                },
            }],
            "missing": {"bins": [], "any_bins": [], "env": ["MY_API_KEY"], "config": []},
            "plan": [],
        }
        output = render_dependency_report(report)
        assert "env: MY_API_KEY" in output

    def test_source_missing_config(self):
        report = {
            "selected_profiles": [],
            "selected": [],
            "sources": [{
                "name": "test",
                "ok": False,
                "missing": {
                    "bins": [],
                    "any_bins": [],
                    "env": [],
                    "config": ["my.config.key"],
                },
            }],
            "missing": {"bins": [], "any_bins": [], "env": [], "config": ["my.config.key"]},
            "plan": [],
        }
        output = render_dependency_report(report)
        assert "config: my.config.key" in output

    def test_apply_result_deferred_step_exit_code_none(self):
        report = {
            "selected_profiles": [],
            "selected": [],
            "sources": [],
            "missing": {"bins": [], "any_bins": [], "env": [], "config": []},
            "plan": [],
            "apply_result": {
                "steps": [{
                    "status": "deferred",
                    "skill": "test",
                    "command": "",
                    "exit_code": None,
                    "stderr": "",
                }]
            },
        }
        output = render_dependency_report(report)
        assert "Apply result:" in output
        assert "[deferred] test:" in output
        assert "exit=" not in output

    def test_apply_result_step_with_stderr(self):
        report = {
            "selected_profiles": [],
            "selected": [],
            "sources": [],
            "missing": {"bins": [], "any_bins": [], "env": [], "config": []},
            "plan": [],
            "apply_result": {
                "steps": [{
                    "status": "failed",
                    "skill": "test",
                    "command": "brew install foo",
                    "exit_code": 1,
                    "stderr": "Error: No such formula",
                }]
            },
        }
        output = render_dependency_report(report)
        assert "stderr: Error: No such formula" in output

    def test_apply_requested_no_steps(self):
        report = {
            "selected_profiles": [],
            "selected": [],
            "sources": [],
            "missing": {"bins": [], "any_bins": [], "env": [], "config": []},
            "plan": [],
            "apply_requested": True,
            "apply_result": {"steps": []},
        }
        output = render_dependency_report(report)
        assert "Apply result: no executable install steps" in output

    def test_apply_requested_without_apply_result(self):
        report = {
            "selected_profiles": [],
            "selected": [],
            "sources": [],
            "missing": {"bins": [], "any_bins": [], "env": [], "config": []},
            "plan": [],
            "apply_requested": True,
        }
        output = render_dependency_report(report)
        assert "Apply result: no executable install steps" in output
