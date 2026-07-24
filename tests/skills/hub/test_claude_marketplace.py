# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.skills.hub._claude_marketplace.

Covers:
- source_id / default marketplaces / custom marketplaces
- search: identifier resolution for "./relative", "owner/repo", and bare source paths
- fetch / inspect: delegate to GitHubSource and relabel the source field
"""

from __future__ import annotations

from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from trpc_agent_sdk.skills.hub import ClaudeMarketplaceSource
from trpc_agent_sdk.skills.hub import GitHubAuth
from trpc_agent_sdk.skills.hub import SkillBundle
from trpc_agent_sdk.skills.hub import SkillMeta
from trpc_agent_sdk.skills.hub._claude_marketplace import DEFAULT_KNOWN_MARKETPLACES


class TestConstruction:

    def test_default_marketplaces(self):
        source = ClaudeMarketplaceSource(GitHubAuth())
        assert source._marketplaces == list(DEFAULT_KNOWN_MARKETPLACES)

    def test_custom_marketplaces(self):
        source = ClaudeMarketplaceSource(GitHubAuth(), marketplaces=["custom/repo"])
        assert source._marketplaces == ["custom/repo"]

    def test_source_id(self):
        assert ClaudeMarketplaceSource(GitHubAuth()).source_id() == "claude-marketplace"


class TestSearch:

    def test_resolves_relative_source_path(self):
        source = ClaudeMarketplaceSource(GitHubAuth(), marketplaces=["anthropics/skills"])
        plugins = [{"name": "docx", "description": "Word docs", "source": "./plugins/docx"}]
        with patch.object(source, "_fetch_marketplace_index", return_value=plugins):
            results = source.search("docx")
        assert len(results) == 1
        assert results[0].identifier == "anthropics/skills/plugins/docx"
        assert results[0].repo == "anthropics/skills"

    def test_resolves_absolute_source_path(self):
        source = ClaudeMarketplaceSource(GitHubAuth(), marketplaces=["anthropics/skills"])
        plugins = [{"name": "docx", "description": "Word docs", "source": "other/repo/docx"}]
        with patch.object(source, "_fetch_marketplace_index", return_value=plugins):
            results = source.search("docx")
        assert results[0].identifier == "other/repo/docx"

    def test_resolves_bare_source_path(self):
        source = ClaudeMarketplaceSource(GitHubAuth(), marketplaces=["anthropics/skills"])
        plugins = [{"name": "docx", "description": "Word docs", "source": "docx"}]
        with patch.object(source, "_fetch_marketplace_index", return_value=plugins):
            results = source.search("docx")
        assert results[0].identifier == "anthropics/skills/docx"

    def test_filters_non_matching_plugins(self):
        source = ClaudeMarketplaceSource(GitHubAuth(), marketplaces=["anthropics/skills"])
        plugins = [{"name": "docx", "description": "Word docs", "source": "docx"}]
        with patch.object(source, "_fetch_marketplace_index", return_value=plugins):
            assert source.search("notfound") == []

    def test_respects_limit_across_marketplaces(self):
        source = ClaudeMarketplaceSource(GitHubAuth(), marketplaces=["repo1", "repo2"])
        plugins = [{"name": "docx", "description": "", "source": "docx"}]
        with patch.object(source, "_fetch_marketplace_index", return_value=plugins):
            results = source.search("docx", limit=1)
        assert len(results) == 1


class TestFetch:

    def test_delegates_to_github_and_relabels_source(self):
        source = ClaudeMarketplaceSource(GitHubAuth())
        bundle = SkillBundle(name="docx",
                             files={"SKILL.md": "body"},
                             source="github",
                             identifier="anthropics/skills/docx")
        with patch("trpc_agent_sdk.skills.hub._claude_marketplace.GitHubSource") as mock_gh_cls:
            mock_gh_cls.return_value.fetch.return_value = bundle
            result = source.fetch("anthropics/skills/docx")
        assert result is bundle
        assert result.source == "claude-marketplace"

    def test_returns_none_when_github_fetch_fails(self):
        source = ClaudeMarketplaceSource(GitHubAuth())
        with patch("trpc_agent_sdk.skills.hub._claude_marketplace.GitHubSource") as mock_gh_cls:
            mock_gh_cls.return_value.fetch.return_value = None
            assert source.fetch("anthropics/skills/docx") is None


class TestInspect:

    def test_delegates_to_github_and_relabels_source(self):
        source = ClaudeMarketplaceSource(GitHubAuth())
        meta = SkillMeta(name="docx", description="", source="github", identifier="anthropics/skills/docx")
        with patch("trpc_agent_sdk.skills.hub._claude_marketplace.GitHubSource") as mock_gh_cls:
            mock_gh_cls.return_value.inspect.return_value = meta
            result = source.inspect("anthropics/skills/docx")
        assert result is meta
        assert result.source == "claude-marketplace"

    def test_returns_none_when_github_inspect_fails(self):
        source = ClaudeMarketplaceSource(GitHubAuth())
        with patch("trpc_agent_sdk.skills.hub._claude_marketplace.GitHubSource") as mock_gh_cls:
            mock_gh_cls.return_value.inspect.return_value = None
            assert source.inspect("anthropics/skills/docx") is None


class TestFetchMarketplaceIndex:

    def test_parses_plugins_from_marketplace_json(self):
        source = ClaudeMarketplaceSource(GitHubAuth())
        resp = MagicMock()
        resp.status_code = 200
        resp.text = '{"plugins": [{"name": "docx"}]}'
        with patch("trpc_agent_sdk.skills.hub._claude_marketplace.httpx.get", return_value=resp):
            plugins = source._fetch_marketplace_index("anthropics/skills")
        assert plugins == [{"name": "docx"}]

    def test_returns_empty_on_non_200(self):
        source = ClaudeMarketplaceSource(GitHubAuth())
        resp = MagicMock()
        resp.status_code = 404
        with patch("trpc_agent_sdk.skills.hub._claude_marketplace.httpx.get", return_value=resp):
            assert source._fetch_marketplace_index("anthropics/skills") == []

    def test_returns_empty_on_invalid_json(self):
        source = ClaudeMarketplaceSource(GitHubAuth())
        resp = MagicMock()
        resp.status_code = 200
        resp.text = "not json"
        with patch("trpc_agent_sdk.skills.hub._claude_marketplace.httpx.get", return_value=resp):
            assert source._fetch_marketplace_index("anthropics/skills") == []
