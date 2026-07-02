# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.skills.hub._github.

Covers:
- GitHubAuth: headers with/without a token
- GitHubSource.source_id / is_rate_limited
- _parse_frontmatter_quick: valid, missing, and malformed YAML frontmatter
- inspect: builds SkillMeta from SKILL.md frontmatter (incl. hermes tags override)
- fetch: builds SkillBundle from a directory tree, rejects dirs without SKILL.md
- search: filters taps by query, dedupes by name, respects limit
- rate limit detection on a 403 response with exhausted quota
"""

from __future__ import annotations

from unittest.mock import MagicMock
from unittest.mock import patch

import httpx
import pytest

from trpc_agent_sdk.skills.hub import GitHubAuth
from trpc_agent_sdk.skills.hub import GitHubSource


def _resp(status_code=200, json_data=None, text="", headers=None):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.text = text
    resp.headers = headers or {}
    return resp


class TestGitHubAuth:

    def test_no_token_unauthenticated(self):
        auth = GitHubAuth()
        assert auth.is_authenticated() is False
        headers = auth.get_headers()
        assert headers["Accept"] == "application/vnd.github.v3+json"
        assert "Authorization" not in headers

    def test_with_token_authenticated(self):
        auth = GitHubAuth("secret-pat")
        assert auth.is_authenticated() is True
        headers = auth.get_headers()
        assert headers["Authorization"] == "token secret-pat"

    def test_empty_token_is_unauthenticated(self):
        auth = GitHubAuth("")
        assert auth.is_authenticated() is False
        assert "Authorization" not in auth.get_headers()


class TestSourceId:

    def test_source_id(self):
        assert GitHubSource(GitHubAuth()).source_id() == "github"

    def test_not_rate_limited_initially(self):
        assert GitHubSource(GitHubAuth()).is_rate_limited is False


class TestParseFrontmatterQuick:

    def test_valid_frontmatter(self):
        content = "---\nname: plan\ndescription: A planning skill\n---\nBody text"
        fm = GitHubSource._parse_frontmatter_quick(content)
        assert fm == {"name": "plan", "description": "A planning skill"}

    def test_no_frontmatter_delimiter(self):
        assert GitHubSource._parse_frontmatter_quick("just a body") == {}

    def test_unterminated_frontmatter(self):
        assert GitHubSource._parse_frontmatter_quick("---\nname: plan\nno closing delimiter") == {}

    def test_malformed_yaml_returns_empty(self):
        content = "---\nname: [unterminated\n---\nbody"
        assert GitHubSource._parse_frontmatter_quick(content) == {}

    def test_non_dict_yaml_returns_empty(self):
        content = "---\n- a\n- b\n---\nbody"
        assert GitHubSource._parse_frontmatter_quick(content) == {}


class TestInspect:

    def test_returns_none_for_malformed_identifier(self):
        source = GitHubSource(GitHubAuth())
        assert source.inspect("owner-only") is None

    def test_builds_meta_from_frontmatter(self):
        source = GitHubSource(GitHubAuth())
        content = "---\nname: plan\ndescription: Plan things\ntags: [a, b]\n---\nBody"
        with patch.object(source, "_fetch_file_content", return_value=content):
            meta = source.inspect("owner/repo/skills/plan")
        assert meta is not None
        assert meta.name == "plan"
        assert meta.description == "Plan things"
        assert meta.source == "github"
        assert meta.identifier == "owner/repo/skills/plan"
        assert meta.repo == "owner/repo"
        assert meta.path == "skills/plan"
        assert meta.tags == ["a", "b"]

    def test_hermes_metadata_tags_take_priority(self):
        source = GitHubSource(GitHubAuth())
        content = (
            "---\n"
            "name: plan\n"
            "description: Plan things\n"
            "tags: [fallback]\n"
            "metadata:\n"
            "  hermes:\n"
            "    tags: [priority]\n"
            "---\n"
            "Body"
        )
        with patch.object(source, "_fetch_file_content", return_value=content):
            meta = source.inspect("owner/repo/skills/plan")
        assert meta.tags == ["priority"]

    def test_empty_hermes_tags_list_falls_back_to_raw_tags(self):
        # An empty (but present) `metadata.hermes.tags: []` must not shadow a
        # populated top-level `tags:` list.
        source = GitHubSource(GitHubAuth())
        content = (
            "---\n"
            "name: plan\n"
            "description: Plan things\n"
            "tags: [a, b]\n"
            "metadata:\n"
            "  hermes:\n"
            "    tags: []\n"
            "---\n"
            "Body"
        )
        with patch.object(source, "_fetch_file_content", return_value=content):
            meta = source.inspect("owner/repo/skills/plan")
        assert meta.tags == ["a", "b"]

    def test_returns_none_when_content_missing(self):
        source = GitHubSource(GitHubAuth())
        with patch.object(source, "_fetch_file_content", return_value=None):
            assert source.inspect("owner/repo/plan") is None

    def test_falls_back_to_dir_name_when_frontmatter_lacks_name(self):
        source = GitHubSource(GitHubAuth())
        content = "---\ndescription: no name field\n---\nBody"
        with patch.object(source, "_fetch_file_content", return_value=content):
            meta = source.inspect("owner/repo/skills/plan")
        assert meta.name == "plan"


class TestFetch:

    def test_returns_none_for_malformed_identifier(self):
        source = GitHubSource(GitHubAuth())
        assert source.fetch("owner-only") is None

    def test_returns_none_when_no_skill_md(self):
        source = GitHubSource(GitHubAuth())
        with patch.object(source, "_download_directory", return_value={"other.txt": "x"}):
            assert source.fetch("owner/repo/skills/plan") is None

    def test_returns_none_when_directory_empty(self):
        source = GitHubSource(GitHubAuth())
        with patch.object(source, "_download_directory", return_value={}):
            assert source.fetch("owner/repo/skills/plan") is None

    def test_builds_bundle_from_downloaded_files(self):
        source = GitHubSource(GitHubAuth())
        files = {"SKILL.md": "---\nname: plan\n---\nbody", "scripts/run.sh": "echo hi"}
        with patch.object(source, "_download_directory", return_value=files):
            bundle = source.fetch("owner/repo/skills/plan")
        assert bundle is not None
        assert bundle.name == "plan"
        assert bundle.files == files
        assert bundle.source == "github"
        assert bundle.identifier == "owner/repo/skills/plan"


class TestSearch:

    def test_no_taps_returns_empty(self):
        source = GitHubSource(GitHubAuth())
        assert source.search("anything") == []

    def test_filters_by_query_across_taps(self):
        source = GitHubSource(GitHubAuth(), taps=[{"repo": "owner/repo", "path": "skills/"}])

        def fake_list_skills_in_repo(repo, path):
            from trpc_agent_sdk.skills.hub import SkillMeta
            return [
                SkillMeta(name="plan", description="planning skill", source="github", identifier="owner/repo/skills/plan"),
                SkillMeta(name="docx", description="word docs", source="github", identifier="owner/repo/skills/docx"),
            ]

        with patch.object(source, "_list_skills_in_repo", side_effect=fake_list_skills_in_repo):
            results = source.search("plan")
        assert len(results) == 1
        assert results[0].name == "plan"

    def test_dedupes_by_name_and_respects_limit(self):
        source = GitHubSource(
            GitHubAuth(),
            taps=[{"repo": "owner/repo1"}, {"repo": "owner/repo2"}],
        )

        def fake_list_skills_in_repo(repo, path):
            from trpc_agent_sdk.skills.hub import SkillMeta
            return [SkillMeta(name="plan", description="", source="github", identifier=f"{repo}/plan")]

        with patch.object(source, "_list_skills_in_repo", side_effect=fake_list_skills_in_repo):
            results = source.search("", limit=10)
        assert len(results) == 1

    def test_tap_error_is_skipped(self):
        source = GitHubSource(GitHubAuth(), taps=[{"repo": "owner/repo"}])
        with patch.object(source, "_list_skills_in_repo", side_effect=RuntimeError("boom")):
            assert source.search("anything") == []


class TestRateLimit:

    def test_flags_rate_limited_on_403_with_exhausted_quota(self):
        source = GitHubSource(GitHubAuth())
        resp = _resp(status_code=403, headers={"X-RateLimit-Remaining": "0"})
        source._check_rate_limit_response(resp)
        assert source.is_rate_limited is True

    def test_403_with_remaining_quota_is_not_rate_limited(self):
        source = GitHubSource(GitHubAuth())
        resp = _resp(status_code=403, headers={"X-RateLimit-Remaining": "10"})
        source._check_rate_limit_response(resp)
        assert source.is_rate_limited is False

    def test_non_403_does_not_flag_rate_limit(self):
        source = GitHubSource(GitHubAuth())
        resp = _resp(status_code=404)
        source._check_rate_limit_response(resp)
        assert source.is_rate_limited is False

    def test_fetch_file_content_flags_rate_limit_via_httpx_get(self):
        source = GitHubSource(GitHubAuth())
        resp = _resp(status_code=403, headers={"X-RateLimit-Remaining": "0"})
        with patch("trpc_agent_sdk.skills.hub._github.httpx.get", return_value=resp):
            content = source._fetch_file_content("owner/repo", "SKILL.md")
        assert content is None
        assert source.is_rate_limited is True


class TestFetchFileContent:

    def test_returns_text_on_200(self):
        source = GitHubSource(GitHubAuth())
        resp = _resp(status_code=200, text="file contents")
        with patch("trpc_agent_sdk.skills.hub._github.httpx.get", return_value=resp):
            assert source._fetch_file_content("owner/repo", "SKILL.md") == "file contents"

    def test_returns_none_on_http_error(self):
        source = GitHubSource(GitHubAuth())
        with patch(
            "trpc_agent_sdk.skills.hub._github.httpx.get",
            side_effect=httpx.ConnectError("boom"),
        ):
            assert source._fetch_file_content("owner/repo", "SKILL.md") is None


class TestDownloadDirectory:

    def test_falls_back_to_contents_api_when_tree_unavailable(self):
        source = GitHubSource(GitHubAuth())
        with patch.object(source, "_download_directory_via_tree", return_value=None), \
             patch.object(source, "_download_directory_recursive", return_value={"SKILL.md": "body"}) as recursive_mock:
            files = source._download_directory("owner/repo", "skills/plan")
        recursive_mock.assert_called_once_with("owner/repo", "skills/plan")
        assert files == {"SKILL.md": "body"}

    def test_uses_tree_result_when_available(self):
        source = GitHubSource(GitHubAuth())
        with patch.object(source, "_download_directory_via_tree", return_value={"SKILL.md": "body"}), \
             patch.object(source, "_download_directory_recursive") as recursive_mock:
            files = source._download_directory("owner/repo", "skills/plan")
        recursive_mock.assert_not_called()
        assert files == {"SKILL.md": "body"}

    def test_download_directory_via_tree_path_not_found_returns_empty_dict(self):
        source = GitHubSource(GitHubAuth())
        with patch.object(source, "_get_repo_tree", return_value=("main", [{"type": "blob", "path": "other/file.txt"}])):
            result = source._download_directory_via_tree("owner/repo", "skills/plan")
        assert result == {}

    def test_download_directory_via_tree_none_when_tree_unavailable(self):
        source = GitHubSource(GitHubAuth())
        with patch.object(source, "_get_repo_tree", return_value=None):
            assert source._download_directory_via_tree("owner/repo", "skills/plan") is None

    def test_download_directory_via_tree_fetches_matching_blobs(self):
        source = GitHubSource(GitHubAuth())
        tree_entries = [
            {"type": "blob", "path": "skills/plan/SKILL.md"},
            {"type": "blob", "path": "skills/plan/scripts/run.sh"},
            {"type": "tree", "path": "skills/plan/scripts"},
            {"type": "blob", "path": "skills/other/SKILL.md"},
        ]
        with patch.object(source, "_get_repo_tree", return_value=("main", tree_entries)), \
             patch.object(source, "_fetch_file_content", side_effect=lambda repo, path: f"content:{path}"):
            files = source._download_directory_via_tree("owner/repo", "skills/plan")
        assert files == {
            "SKILL.md": "content:skills/plan/SKILL.md",
            "scripts/run.sh": "content:skills/plan/scripts/run.sh",
        }


class TestFindSkillInRepoTree:

    def test_finds_skill_dir_by_suffix_match(self):
        source = GitHubSource(GitHubAuth())
        tree_entries = [
            {"type": "blob", "path": "components/skills/dev/plan/SKILL.md"},
        ]
        with patch.object(source, "_get_repo_tree", return_value=("main", tree_entries)):
            result = source._find_skill_in_repo_tree("owner/repo", "plan")
        assert result == "owner/repo/components/skills/dev/plan"

    def test_returns_none_when_not_found(self):
        source = GitHubSource(GitHubAuth())
        with patch.object(source, "_get_repo_tree", return_value=("main", [])):
            assert source._find_skill_in_repo_tree("owner/repo", "plan") is None

    def test_returns_none_when_tree_unavailable(self):
        source = GitHubSource(GitHubAuth())
        with patch.object(source, "_get_repo_tree", return_value=None):
            assert source._find_skill_in_repo_tree("owner/repo", "plan") is None
