# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.skills.hub._hermes_index.

Covers:
- source_id / is_available
- search: empty query returns featured, non-empty filters by text, respects limit
- inspect / _find_entry: exact match, prefix-normalized match, no match
- fetch: resolved_github_id delegation, repo+path fallback delegation, no-match
- index loading is cached across calls (single fetch)
"""

from __future__ import annotations

from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from trpc_agent_sdk.skills.hub import GitHubAuth
from trpc_agent_sdk.skills.hub import SkillBundle
from trpc_agent_sdk.skills.hub._hermes_index import HermesIndexSource

_INDEX = {
    "skills": [
        {
            "identifier": "owner/repo/skills/plan",
            "name": "plan",
            "description": "Plan things",
            "tags": ["planning"],
            "source": "github",
            "repo": "owner/repo",
            "path": "skills/plan",
        },
        {
            "identifier": "skills-sh/owner2/repo2/docx",
            "name": "docx",
            "description": "Word docs",
            "tags": [],
            "source": "skills.sh",
            "repo": "owner2/repo2",
            "path": "docx",
        },
    ]
}


class TestSourceId:

    def test_source_id(self):
        assert HermesIndexSource(GitHubAuth()).source_id() == "hermes-index"


class TestIsAvailable:

    def test_unavailable_when_index_load_fails(self):
        source = HermesIndexSource(GitHubAuth())
        with patch("trpc_agent_sdk.skills.hub._hermes_index._load_hermes_index", return_value=None):
            assert source.is_available is False

    def test_available_when_index_has_skills(self):
        source = HermesIndexSource(GitHubAuth())
        with patch("trpc_agent_sdk.skills.hub._hermes_index._load_hermes_index", return_value=_INDEX):
            assert source.is_available is True

    def test_index_is_loaded_only_once(self):
        source = HermesIndexSource(GitHubAuth())
        with patch("trpc_agent_sdk.skills.hub._hermes_index._load_hermes_index", return_value=_INDEX) as load_mock:
            assert source.is_available is True
            assert source.is_available is True
        load_mock.assert_called_once()


class TestSearch:

    def test_empty_query_returns_featured_up_to_limit(self):
        source = HermesIndexSource(GitHubAuth())
        with patch("trpc_agent_sdk.skills.hub._hermes_index._load_hermes_index", return_value=_INDEX):
            results = source.search("", limit=1)
        assert len(results) == 1
        assert results[0].name == "plan"

    def test_filters_by_text(self):
        source = HermesIndexSource(GitHubAuth())
        with patch("trpc_agent_sdk.skills.hub._hermes_index._load_hermes_index", return_value=_INDEX):
            results = source.search("docx")
        assert len(results) == 1
        assert results[0].name == "docx"

    def test_no_skills_in_index_returns_empty(self):
        source = HermesIndexSource(GitHubAuth())
        with patch("trpc_agent_sdk.skills.hub._hermes_index._load_hermes_index", return_value={"skills": []}):
            assert source.search("anything") == []

    def test_unavailable_index_returns_empty(self):
        source = HermesIndexSource(GitHubAuth())
        with patch("trpc_agent_sdk.skills.hub._hermes_index._load_hermes_index", return_value=None):
            assert source.search("anything") == []


class TestFindEntryAndInspect:

    def test_exact_identifier_match(self):
        source = HermesIndexSource(GitHubAuth())
        with patch("trpc_agent_sdk.skills.hub._hermes_index._load_hermes_index", return_value=_INDEX):
            meta = source.inspect("owner/repo/skills/plan")
        assert meta is not None
        assert meta.name == "plan"

    def test_prefix_normalized_match(self):
        source = HermesIndexSource(GitHubAuth())
        with patch("trpc_agent_sdk.skills.hub._hermes_index._load_hermes_index", return_value=_INDEX):
            meta = source.inspect("owner2/repo2/docx")
        assert meta is not None
        assert meta.name == "docx"

    def test_no_match_returns_none(self):
        source = HermesIndexSource(GitHubAuth())
        with patch("trpc_agent_sdk.skills.hub._hermes_index._load_hermes_index", return_value=_INDEX):
            assert source.inspect("nonexistent") is None


class TestFetch:

    def test_uses_resolved_github_id_when_present(self):
        source = HermesIndexSource(GitHubAuth())
        index = {
            "skills": [{
                "identifier": "owner/repo/skills/plan",
                "name": "plan",
                "resolved_github_id": "owner/repo/actual/path/plan",
                "source": "github",
            }]
        }
        bundle = SkillBundle(name="plan", files={"SKILL.md": "body"}, source="github", identifier="resolved")
        fake_github = MagicMock()
        fake_github.fetch.return_value = bundle

        with patch("trpc_agent_sdk.skills.hub._hermes_index._load_hermes_index", return_value=index), \
             patch.object(source, "_get_github", return_value=fake_github):
            result = source.fetch("owner/repo/skills/plan")

        fake_github.fetch.assert_called_once_with("owner/repo/actual/path/plan")
        assert result is bundle
        assert result.identifier == "owner/repo/skills/plan"

    def test_falls_back_to_repo_and_path(self):
        source = HermesIndexSource(GitHubAuth())
        index = {
            "skills": [{
                "identifier": "owner/repo/skills/plan",
                "name": "plan",
                "repo": "owner/repo",
                "path": "skills/plan",
                "source": "github",
            }]
        }
        bundle = SkillBundle(name="plan", files={"SKILL.md": "body"}, source="github", identifier="x")
        fake_github = MagicMock()
        fake_github.fetch.return_value = bundle

        with patch("trpc_agent_sdk.skills.hub._hermes_index._load_hermes_index", return_value=index), \
             patch.object(source, "_get_github", return_value=fake_github):
            result = source.fetch("owner/repo/skills/plan")

        fake_github.fetch.assert_called_once_with("owner/repo/skills/plan")
        assert result is bundle

    def test_no_entry_returns_none(self):
        source = HermesIndexSource(GitHubAuth())
        with patch("trpc_agent_sdk.skills.hub._hermes_index._load_hermes_index", return_value=_INDEX):
            assert source.fetch("nonexistent") is None

    def test_github_fetch_failure_returns_none(self):
        source = HermesIndexSource(GitHubAuth())
        fake_github = MagicMock()
        fake_github.fetch.return_value = None
        with patch("trpc_agent_sdk.skills.hub._hermes_index._load_hermes_index", return_value=_INDEX), \
             patch.object(source, "_get_github", return_value=fake_github):
            assert source.fetch("owner/repo/skills/plan") is None


class TestLoadHermesIndex:

    def test_load_returns_none_on_non_200(self):
        from trpc_agent_sdk.skills.hub._hermes_index import _load_hermes_index

        resp = MagicMock()
        resp.status_code = 404
        with patch("trpc_agent_sdk.skills.hub._hermes_index.httpx.get", return_value=resp):
            assert _load_hermes_index() is None

    def test_load_returns_data_on_200(self):
        from trpc_agent_sdk.skills.hub._hermes_index import _load_hermes_index

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = _INDEX
        with patch("trpc_agent_sdk.skills.hub._hermes_index.httpx.get", return_value=resp):
            assert _load_hermes_index() == _INDEX

    def test_load_returns_none_when_skills_key_missing(self):
        from trpc_agent_sdk.skills.hub._hermes_index import _load_hermes_index

        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"no_skills_key": True}
        with patch("trpc_agent_sdk.skills.hub._hermes_index.httpx.get", return_value=resp):
            assert _load_hermes_index() is None
