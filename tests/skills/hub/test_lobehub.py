# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.skills.hub._lobehub.

Covers:
- source_id
- search: filters agents by query text, respects the "lobehub/" identifier prefix
- inspect: exact identifier match against the index
- fetch: converts the agent JSON into a synthetic SKILL.md bundle
- _convert_to_skill_md: frontmatter + body shape
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from trpc_agent_sdk.skills.hub import LobeHubSource


_INDEX = {
    "agents": [
        {
            "identifier": "writer-bot",
            "meta": {"title": "Writer Bot", "description": "Helps you write.", "tags": ["writing"]},
        },
        {
            "identifier": "coder-bot",
            "meta": {"title": "Coder Bot", "description": "Helps you code.", "tags": ["coding"]},
        },
    ]
}


class TestSourceId:

    def test_source_id(self):
        assert LobeHubSource().source_id() == "lobehub"


class TestSearch:

    def test_filters_by_query(self):
        source = LobeHubSource()
        with patch.object(source, "_fetch_index", return_value=_INDEX):
            results = source.search("write")
        assert len(results) == 1
        assert results[0].identifier == "lobehub/writer-bot"

    def test_index_unavailable_returns_empty(self):
        source = LobeHubSource()
        with patch.object(source, "_fetch_index", return_value=None):
            assert source.search("anything") == []

    def test_respects_limit(self):
        source = LobeHubSource()
        with patch.object(source, "_fetch_index", return_value=_INDEX):
            results = source.search("bot", limit=1)
        assert len(results) == 1


class TestInspect:

    def test_finds_exact_identifier(self):
        source = LobeHubSource()
        with patch.object(source, "_fetch_index", return_value=_INDEX):
            meta = source.inspect("lobehub/writer-bot")
        assert meta is not None
        assert meta.identifier == "lobehub/writer-bot"
        assert meta.description == "Helps you write."

    def test_strips_lobehub_prefix(self):
        source = LobeHubSource()
        with patch.object(source, "_fetch_index", return_value=_INDEX):
            meta = source.inspect("writer-bot")
        assert meta is not None

    def test_returns_none_when_not_found(self):
        source = LobeHubSource()
        with patch.object(source, "_fetch_index", return_value=_INDEX):
            assert source.inspect("nonexistent") is None

    def test_returns_none_when_index_unavailable(self):
        source = LobeHubSource()
        with patch.object(source, "_fetch_index", return_value=None):
            assert source.inspect("writer-bot") is None


class TestFetch:

    def test_builds_bundle_from_agent_json(self):
        source = LobeHubSource()
        agent_data = {
            "identifier": "writer-bot",
            "meta": {"title": "Writer Bot", "description": "Helps you write.", "tags": ["writing"]},
            "config": {"systemRole": "You are a writing assistant."},
        }
        with patch.object(source, "_fetch_agent", return_value=agent_data):
            bundle = source.fetch("lobehub/writer-bot")
        assert bundle is not None
        assert bundle.name == "writer-bot"
        assert bundle.identifier == "lobehub/writer-bot"
        assert "SKILL.md" in bundle.files
        assert "Writer Bot" in bundle.files["SKILL.md"]
        assert "You are a writing assistant." in bundle.files["SKILL.md"]

    def test_returns_none_when_agent_fetch_fails(self):
        source = LobeHubSource()
        with patch.object(source, "_fetch_agent", return_value=None):
            assert source.fetch("lobehub/writer-bot") is None


class TestConvertToSkillMd:

    def test_includes_frontmatter_and_body(self):
        agent_data = {
            "identifier": "writer-bot",
            "meta": {"title": "Writer Bot", "description": "Helps you write.", "tags": ["writing", "assistant"]},
            "config": {"systemRole": "Be helpful."},
        }
        md = LobeHubSource._convert_to_skill_md(agent_data)
        assert md.startswith("---\n")
        assert "name: writer-bot" in md
        assert "# Writer Bot" in md
        assert "Be helpful." in md

    def test_missing_system_role_uses_placeholder(self):
        agent_data = {"identifier": "writer-bot", "meta": {"title": "Writer Bot"}}
        md = LobeHubSource._convert_to_skill_md(agent_data)
        assert "(No system role defined)" in md
