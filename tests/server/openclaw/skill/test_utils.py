"""Unit tests for trpc_agent_sdk.server.openclaw.skill._utils."""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from trpc_agent_sdk.server.openclaw.skill._utils import (
    camel_to_snake,
    extract_archive,
    normalize_allowlist,
    normalize_bundled_root,
    normalize_config_key,
    normalize_config_keys,
    normalize_skill_configs,
    prepare_dir,
    skill_file_in_dir,
    strip_frontmatter,
)


class TestPrepareDir:

    def test_creates_new_directory(self, tmp_path):
        target = tmp_path / "new_dir"
        assert not target.exists()
        prepare_dir(target)
        assert target.is_dir()

    def test_removes_existing_then_recreates(self, tmp_path):
        target = tmp_path / "existing"
        target.mkdir()
        sentinel = target / "old_file.txt"
        sentinel.write_text("old", encoding="utf-8")
        prepare_dir(target)
        assert target.is_dir()
        assert not sentinel.exists()

    def test_creates_nested_parents(self, tmp_path):
        target = tmp_path / "a" / "b" / "c"
        prepare_dir(target)
        assert target.is_dir()


class TestSkillFileInDir:

    def test_finds_uppercase_skill_md(self, tmp_path):
        (tmp_path / "SKILL.md").write_text("content", encoding="utf-8")
        result = skill_file_in_dir(tmp_path)
        assert result == tmp_path / "SKILL.md"

    def test_finds_lowercase_skill_md(self, tmp_path):
        (tmp_path / "skill.md").write_text("content", encoding="utf-8")
        result = skill_file_in_dir(tmp_path)
        assert result is not None
        assert result.name.lower() == "skill.md"

    def test_returns_none_when_missing(self, tmp_path):
        assert skill_file_in_dir(tmp_path) is None


class TestExtractArchive:

    def test_extract_zip(self, tmp_path):
        zip_path = tmp_path / "test.zip"
        dest = tmp_path / "out"
        dest.mkdir()
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("hello.txt", "hello world")
        extract_archive(zip_path, dest)
        assert (dest / "hello.txt").read_text() == "hello world"

    def test_extract_tar(self, tmp_path):
        tar_path = tmp_path / "test.tar"
        dest = tmp_path / "out"
        dest.mkdir()
        source_file = tmp_path / "sample.txt"
        source_file.write_text("tar content", encoding="utf-8")
        with tarfile.open(tar_path, "w:") as tf:
            tf.add(source_file, arcname="sample.txt")
        extract_archive(tar_path, dest)
        assert (dest / "sample.txt").read_text() == "tar content"

    def test_extract_tar_gz(self, tmp_path):
        tar_path = tmp_path / "test.tar.gz"
        dest = tmp_path / "out"
        dest.mkdir()
        source_file = tmp_path / "data.txt"
        source_file.write_text("gz content", encoding="utf-8")
        with tarfile.open(tar_path, "w:gz") as tf:
            tf.add(source_file, arcname="data.txt")
        extract_archive(tar_path, dest)
        assert (dest / "data.txt").read_text() == "gz content"

    def test_extract_tgz(self, tmp_path):
        tgz_path = tmp_path / "test.tgz"
        dest = tmp_path / "out"
        dest.mkdir()
        source_file = tmp_path / "tgz_file.txt"
        source_file.write_text("tgz content", encoding="utf-8")
        with tarfile.open(tgz_path, "w:gz") as tf:
            tf.add(source_file, arcname="tgz_file.txt")
        extract_archive(tgz_path, dest)
        assert (dest / "tgz_file.txt").read_text() == "tgz content"

    def test_unsupported_raises_value_error(self, tmp_path):
        bad_file = tmp_path / "test.rar"
        bad_file.write_text("nope", encoding="utf-8")
        dest = tmp_path / "out"
        dest.mkdir()
        with pytest.raises(ValueError, match="unsupported archive type"):
            extract_archive(bad_file, dest)


class TestStripFrontmatter:

    def test_with_frontmatter(self):
        content = "---\nname: test\ntags: [a]\n---\nBody here"
        result = strip_frontmatter(content)
        assert result == "Body here"

    def test_without_frontmatter(self):
        content = "Just plain text"
        assert strip_frontmatter(content) == content

    def test_incomplete_frontmatter(self):
        content = "---\nname: test\nNo closing delimiter"
        assert strip_frontmatter(content) == content

    def test_empty_body_after_frontmatter(self):
        content = "---\nname: test\n---\n"
        result = strip_frontmatter(content)
        assert result == ""


class TestNormalizeBundledRoot:

    def test_empty_input(self):
        assert normalize_bundled_root("") == ""
        assert normalize_bundled_root(None) == ""

    def test_valid_path(self, tmp_path):
        result = normalize_bundled_root(str(tmp_path))
        assert result == str(tmp_path.resolve())

    def test_invalid_path_returns_value(self):
        result = normalize_bundled_root("/nonexistent/path/xyz_abc_123")
        assert result == "/nonexistent/path/xyz_abc_123"


class TestNormalizeConfigKey:

    def test_trim_and_lowercase(self):
        assert normalize_config_key("  FooBar  ") == "foobar"

    def test_already_normal(self):
        assert normalize_config_key("key") == "key"


class TestNormalizeConfigKeys:

    def test_empty_list(self):
        assert normalize_config_keys([]) == set()

    def test_normal_list(self):
        result = normalize_config_keys(["  Foo  ", "Bar", "  ", "baz"])
        assert result == {"foo", "bar", "baz"}

    def test_deduplicates(self):
        result = normalize_config_keys(["abc", "ABC", " abc "])
        assert result == {"abc"}


class TestNormalizeAllowlist:

    def test_not_list(self):
        assert normalize_allowlist("not_a_list") == set()
        assert normalize_allowlist(None) == set()
        assert normalize_allowlist(42) == set()

    def test_empty_list(self):
        assert normalize_allowlist([]) == set()

    def test_normal_list(self):
        result = normalize_allowlist(["  alpha  ", "beta", "  ", "gamma"])
        assert result == {"alpha", "beta", "gamma"}


class TestNormalizeSkillConfigs:

    def test_not_dict(self):
        assert normalize_skill_configs("nope") == {}
        assert normalize_skill_configs(None) == {}
        assert normalize_skill_configs(42) == {}

    def test_empty_dict(self):
        assert normalize_skill_configs({}) == {}

    def test_normal_dict_with_model_dump(self):
        mock_cfg = MagicMock()
        mock_cfg.model_dump.return_value = {"enabled": True, "env": {"K": "V"}}
        result = normalize_skill_configs({"myskill": mock_cfg})
        assert "myskill" in result
        assert result["myskill"]["enabled"] is True
        assert result["myskill"]["env"] == {"K": "V"}

    def test_dict_config_input(self):
        result = normalize_skill_configs({"sk": {"enabled": False, "env": {"A": "B"}}})
        assert "sk" in result
        assert result["sk"]["enabled"] is False

    def test_empty_key_skipped(self):
        result = normalize_skill_configs({"  ": {"enabled": True, "env": {}}})
        assert result == {}

    def test_non_dict_non_model_cfg_skipped(self):
        result = normalize_skill_configs({"sk": "invalid_value"})
        assert result == {}

    def test_env_strips_empty(self):
        result = normalize_skill_configs({"sk": {"enabled": True, "env": {"  ": "v", "k": "  "}}})
        assert result["sk"]["env"] == {}


class TestCamelToSnake:

    @pytest.mark.parametrize("input_val,expected", [
        ("CamelCase", "camel_case"),
        ("camelCase", "camel_case"),
        ("HTTPResponse", "h_t_t_p_response"),
        ("simple", "simple"),
        ("A", "a"),
        ("ABC", "a_b_c"),
        ("getHTTPResponseCode", "get_h_t_t_p_response_code"),
    ])
    def test_conversion(self, input_val, expected):
        assert camel_to_snake(input_val) == expected
