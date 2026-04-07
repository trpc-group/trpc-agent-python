"""Unit tests for trpc_agent_sdk.server.openclaw.skill._skill_parser."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trpc_agent_sdk.server.openclaw.config import SkillConfig, SkillRootConfig
from trpc_agent_sdk.server.openclaw.skill._skill_parser import ClawSkillParser


def _make_root_config(
    config_keys=None,
    allow_bundled=None,
    skill_configs=None,
) -> SkillRootConfig:
    return SkillRootConfig(
        config_keys=config_keys or [],
        allow_bundled=allow_bundled or [],
        skill_configs=skill_configs or {},
    )


class TestClawSkillParserInit:

    def test_normalizes_config_keys(self):
        parser = ClawSkillParser(_make_root_config(config_keys=["  FooBar  ", "baz"]))
        assert parser._config_keys == {"foobar", "baz"}

    def test_normalizes_allow_bundled(self):
        parser = ClawSkillParser(_make_root_config(allow_bundled=["  alpha  ", "beta"]))
        assert parser._allow_bundled == {"alpha", "beta"}

    def test_normalizes_skill_configs(self):
        cfg = SkillConfig(enabled=False, env={"KEY": "VAL"})
        parser = ClawSkillParser(_make_root_config(skill_configs={"  myskill  ": cfg}))
        assert "myskill" in parser._skill_configs
        assert parser._skill_configs["myskill"].enabled is False

    def test_empty_defaults(self):
        parser = ClawSkillParser(_make_root_config())
        assert parser._config_keys == set()
        assert parser._allow_bundled == set()
        assert parser._skill_configs == {}


class TestParseMetadata:

    def setup_method(self):
        self.parser = ClawSkillParser(_make_root_config())

    def test_empty_input(self):
        assert self.parser.parse_metadata(None) == {}
        assert self.parser.parse_metadata({}) == {}
        assert self.parser.parse_metadata("") == {}

    def test_whitespace_string(self):
        assert self.parser.parse_metadata("   ") == {}

    def test_json_string(self):
        raw = json.dumps({"always": True, "skill_key": "x"})
        result = self.parser.parse_metadata(raw)
        assert result == {"always": True, "skill_key": "x"}

    def test_python_literal_string(self):
        raw = "{'always': True, 'skill_key': 'x'}"
        result = self.parser.parse_metadata(raw)
        assert result == {"always": True, "skill_key": "x"}

    def test_invalid_string_returns_empty(self):
        assert self.parser.parse_metadata("not valid json or python") == {}

    def test_dict_with_openclaw_key(self):
        raw = {"openclaw": {"always": True}, "other": "data"}
        result = self.parser.parse_metadata(raw)
        assert result == {"always": True}

    def test_dict_without_openclaw_key(self):
        raw = {"always": False, "skill_key": "test"}
        result = self.parser.parse_metadata(raw)
        assert result == raw

    def test_dict_openclaw_not_dict_treated_as_direct(self):
        raw = {"openclaw": "not a dict", "foo": "bar"}
        result = self.parser.parse_metadata(raw)
        assert result == raw

    def test_json_string_with_openclaw(self):
        raw = json.dumps({"openclaw": {"skill_key": "abc"}})
        result = self.parser.parse_metadata(raw)
        assert result == {"skill_key": "abc"}


class TestEvaluateSkillEligibility:

    def test_disabled_by_config(self):
        cfg = SkillConfig(enabled=False)
        parser = ClawSkillParser(_make_root_config(skill_configs={"myskill": cfg}))
        result = parser.evaluate_skill_eligibility(
            skill_name="myskill",
            source="user",
            skill_meta={"skill_key": "myskill"},
        )
        assert result == "disabled by config"

    def test_blocked_by_allow_bundled(self):
        parser = ClawSkillParser(_make_root_config(allow_bundled=["allowed_skill"]))
        result = parser.evaluate_skill_eligibility(
            skill_name="other_skill",
            source="builtin",
            skill_meta={},
        )
        assert result == "blocked by allow_bundled"

    def test_allow_bundled_permits_listed_name(self):
        parser = ClawSkillParser(_make_root_config(allow_bundled=["good_skill"]))
        result = parser.evaluate_skill_eligibility(
            skill_name="good_skill",
            source="builtin",
            skill_meta={},
        )
        assert result == ""

    def test_allow_bundled_permits_listed_key(self):
        parser = ClawSkillParser(_make_root_config(allow_bundled=["sk"]))
        result = parser.evaluate_skill_eligibility(
            skill_name="other",
            source="builtin",
            skill_meta={"skill_key": "sk"},
        )
        assert result == ""

    def test_always_flag_skips_requires(self):
        parser = ClawSkillParser(_make_root_config())
        result = parser.evaluate_skill_eligibility(
            skill_name="test",
            source="user",
            skill_meta={"always": True, "requires": {"bins": ["nonexistent_bin_xyz"]}},
        )
        assert result == ""

    @patch("trpc_agent_sdk.server.openclaw.skill._skill_parser.sys")
    def test_os_mismatch(self, mock_sys):
        mock_sys.platform = "linux"
        parser = ClawSkillParser(_make_root_config())
        result = parser.evaluate_skill_eligibility(
            skill_name="test",
            source="user",
            skill_meta={"os": ["darwin"]},
        )
        assert "os mismatch" in result

    @patch("trpc_agent_sdk.server.openclaw.skill._skill_parser.sys")
    def test_os_match(self, mock_sys):
        mock_sys.platform = "darwin"
        parser = ClawSkillParser(_make_root_config())
        result = parser.evaluate_skill_eligibility(
            skill_name="test",
            source="user",
            skill_meta={"os": ["darwin"]},
        )
        assert result == ""

    @patch("shutil.which", return_value=None)
    def test_missing_bins(self, mock_which):
        parser = ClawSkillParser(_make_root_config())
        result = parser.evaluate_skill_eligibility(
            skill_name="test",
            source="user",
            skill_meta={"requires": {"bins": ["missing_tool"]}},
        )
        assert "missing bins" in result

    @patch("shutil.which", return_value=None)
    def test_missing_any_bins(self, mock_which):
        parser = ClawSkillParser(_make_root_config())
        result = parser.evaluate_skill_eligibility(
            skill_name="test",
            source="user",
            skill_meta={"requires": {"any_bins": ["tool1", "tool2"]}},
        )
        assert "missing any_bins" in result

    @patch("shutil.which", side_effect=lambda x: "/usr/bin/tool1" if x == "tool1" else None)
    def test_any_bins_one_found(self, mock_which):
        parser = ClawSkillParser(_make_root_config())
        result = parser.evaluate_skill_eligibility(
            skill_name="test",
            source="user",
            skill_meta={"requires": {"any_bins": ["tool1", "tool2"]}},
        )
        assert result == ""

    def test_missing_env(self):
        parser = ClawSkillParser(_make_root_config())
        env_key = "MY_CUSTOM_TEST_ENV_VAR_UNIQUE_12345"
        os.environ.pop(env_key, None)
        result = parser.evaluate_skill_eligibility(
            skill_name="test",
            source="user",
            skill_meta={"requires": {"env": [env_key]}},
        )
        assert "missing env" in result

    def test_env_present_in_os(self):
        parser = ClawSkillParser(_make_root_config())
        env_key = "MY_CUSTOM_TEST_ENV_VAR_UNIQUE_12345"
        os.environ[env_key] = "some_value"
        try:
            result = parser.evaluate_skill_eligibility(
                skill_name="test",
                source="user",
                skill_meta={"requires": {"env": [env_key]}},
            )
            assert result == ""
        finally:
            os.environ.pop(env_key, None)

    def test_env_blocked_key_always_missing(self):
        parser = ClawSkillParser(_make_root_config())
        result = parser.evaluate_skill_eligibility(
            skill_name="test",
            source="user",
            skill_meta={"requires": {"env": ["LD_PRELOAD"]}},
        )
        assert "missing env" in result

    def test_env_provided_by_skill_config(self):
        cfg = SkillConfig(env={"SOME_KEY": "provided_val"})
        parser = ClawSkillParser(_make_root_config(skill_configs={"test": cfg}))
        os.environ.pop("SOME_KEY", None)
        result = parser.evaluate_skill_eligibility(
            skill_name="test",
            source="user",
            skill_meta={"requires": {"env": ["SOME_KEY"]}},
        )
        assert result == ""

    def test_missing_config(self):
        parser = ClawSkillParser(_make_root_config(config_keys=[]))
        result = parser.evaluate_skill_eligibility(
            skill_name="test",
            source="user",
            skill_meta={"requires": {"config": ["needed_key"]}},
        )
        assert "missing config" in result

    def test_config_present(self):
        parser = ClawSkillParser(_make_root_config(config_keys=["needed_key"]))
        result = parser.evaluate_skill_eligibility(
            skill_name="test",
            source="user",
            skill_meta={"requires": {"config": ["needed_key"]}},
        )
        assert result == ""

    def test_all_checks_pass(self):
        parser = ClawSkillParser(_make_root_config())
        result = parser.evaluate_skill_eligibility(
            skill_name="test",
            source="user",
            skill_meta={},
        )
        assert result == ""


class TestHasConfigKey:

    def test_empty_config_keys(self):
        parser = ClawSkillParser(_make_root_config())
        assert parser.has_config_key("anything") is False

    def test_exact_match(self):
        parser = ClawSkillParser(_make_root_config(config_keys=["mykey"]))
        assert parser.has_config_key("mykey") is True

    def test_prefix_match(self):
        parser = ClawSkillParser(_make_root_config(config_keys=["mykey.sub"]))
        assert parser.has_config_key("mykey") is True

    def test_no_match(self):
        parser = ClawSkillParser(_make_root_config(config_keys=["other"]))
        assert parser.has_config_key("mykey") is False


class TestIsBlockedSkillEnvKey:

    @pytest.mark.parametrize("key", [
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "DYLD_FORCE_FLAT_NAMESPACE",
        "OPENSSL_CONF",
        "  ld_preload  ",
    ])
    def test_blocked_keys(self, key):
        assert ClawSkillParser.is_blocked_skill_env_key(key) is True

    @pytest.mark.parametrize("key", [
        "PATH",
        "HOME",
        "MY_CUSTOM_VAR",
        "PYTHONPATH",
    ])
    def test_non_blocked_keys(self, key):
        assert ClawSkillParser.is_blocked_skill_env_key(key) is False


class TestReadSkillName:

    def setup_method(self):
        self.parser = ClawSkillParser(_make_root_config())

    def test_success(self, tmp_path):
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text("---\nname: My Skill\n---\nContent here", encoding="utf-8")

        def mock_from_markdown(content):
            return {"name": "My Skill"}, "Content here"

        result = self.parser.read_skill_name(skill_file, mock_from_markdown)
        assert result == "My Skill"

    def test_empty_name(self, tmp_path):
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text("---\n---\nContent", encoding="utf-8")

        def mock_from_markdown(content):
            return {}, "Content"

        result = self.parser.read_skill_name(skill_file, mock_from_markdown)
        assert result == ""

    def test_exception_returns_empty(self, tmp_path):
        skill_file = tmp_path / "nonexistent.md"

        def mock_from_markdown(content):
            raise RuntimeError("boom")

        result = self.parser.read_skill_name(skill_file, mock_from_markdown)
        assert result == ""


class TestNormalizeHelpers:

    def test_normalize_config_keys_non_list(self):
        result = ClawSkillParser._normalize_config_keys(None)
        assert result == set()

    def test_normalize_allowlist_non_list(self):
        result = ClawSkillParser._normalize_allowlist(None)
        assert result == set()

    def test_normalize_skill_configs_dict_input(self):
        result = ClawSkillParser._normalize_skill_configs({"sk": SkillConfig(enabled=True, env={"K": "V"})})
        assert "sk" in result
        assert result["sk"].enabled is True

    def test_normalize_skill_configs_empty_key_skipped(self):
        result = ClawSkillParser._normalize_skill_configs({"  ": SkillConfig()})
        assert result == {}

    def test_normalize_skill_configs_dict_cfg(self):
        result = ClawSkillParser._normalize_skill_configs({"sk": {"enabled": False, "env": {"A": "B"}}})
        assert "sk" in result
        assert result["sk"].enabled is False


class TestResolveSkillConfig:

    def test_resolved_by_key(self):
        cfg = SkillConfig(enabled=False)
        parser = ClawSkillParser(_make_root_config(skill_configs={"mykey": cfg}))
        result = parser._resolve_skill_config("mykey", "othername")
        assert result.enabled is False

    def test_resolved_by_name(self):
        cfg = SkillConfig(enabled=False)
        parser = ClawSkillParser(_make_root_config(skill_configs={"myname": cfg}))
        result = parser._resolve_skill_config("unknown_key", "myname")
        assert result.enabled is False

    def test_fallback_default(self):
        parser = ClawSkillParser(_make_root_config())
        result = parser._resolve_skill_config("nope", "nada")
        assert result.enabled is None
