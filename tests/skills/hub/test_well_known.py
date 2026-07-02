# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.skills.hub._well_known.

Covers:
- source_id
- _query_to_index_url: URL normalization for different query shapes
- _parse_identifier: index.json#fragment, .../SKILL.md, and bare skill URLs
- search / inspect / fetch against a mocked index + skill files
- fetch rejects unsafe skill names / file paths advertised by a malicious index
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from trpc_agent_sdk.skills.hub._well_known import WellKnownSkillSource


class TestSourceId:

    def test_source_id(self):
        assert WellKnownSkillSource().source_id() == "well-known"


class TestBasePathNormalization:

    def test_default_base_path(self):
        source = WellKnownSkillSource()
        assert source._base_path == "/.well-known/skills"

    def test_custom_base_path_gets_leading_slash(self):
        source = WellKnownSkillSource("custom/skills")
        assert source._base_path == "/custom/skills"

    def test_trailing_slash_stripped(self):
        source = WellKnownSkillSource("/custom/skills/")
        assert source._base_path == "/custom/skills"


class TestQueryToIndexUrl:

    def test_rejects_non_http_query(self):
        source = WellKnownSkillSource()
        assert source._query_to_index_url("not-a-url") is None

    def test_query_ending_in_index_json_is_passthrough(self):
        source = WellKnownSkillSource()
        url = "https://example.com/.well-known/skills/index.json"
        assert source._query_to_index_url(url) == url

    def test_query_with_base_path_segment(self):
        source = WellKnownSkillSource()
        url = "https://example.com/.well-known/skills/plan"
        assert source._query_to_index_url(url) == "https://example.com/.well-known/skills/index.json"

    def test_bare_domain_appends_default_index_path(self):
        source = WellKnownSkillSource()
        assert source._query_to_index_url("https://example.com") == (
            "https://example.com/.well-known/skills/index.json"
        )


class TestParseIdentifier:

    def test_rejects_non_http_identifier(self):
        source = WellKnownSkillSource()
        assert source._parse_identifier("not-a-url") is None

    def test_index_json_with_fragment(self):
        source = WellKnownSkillSource()
        parsed = source._parse_identifier(
            "well-known:https://example.com/.well-known/skills/index.json#plan"
        )
        assert parsed == {
            "index_url": "https://example.com/.well-known/skills/index.json",
            "base_url": "https://example.com/.well-known/skills",
            "skill_name": "plan",
            "skill_url": "https://example.com/.well-known/skills/plan",
        }

    def test_index_json_without_fragment_is_rejected(self):
        source = WellKnownSkillSource()
        assert source._parse_identifier("https://example.com/.well-known/skills/index.json") is None

    def test_skill_md_url(self):
        source = WellKnownSkillSource()
        parsed = source._parse_identifier("https://example.com/.well-known/skills/plan/SKILL.md")
        assert parsed["skill_name"] == "plan"
        assert parsed["base_url"] == "https://example.com/.well-known/skills"

    def test_bare_skill_url(self):
        source = WellKnownSkillSource()
        parsed = source._parse_identifier("https://example.com/.well-known/skills/plan")
        assert parsed["skill_name"] == "plan"

    def test_url_missing_base_path_is_rejected(self):
        source = WellKnownSkillSource()
        assert source._parse_identifier("https://example.com/plan") is None


class TestSearch:

    def test_non_url_query_returns_empty(self):
        source = WellKnownSkillSource()
        assert source.search("plan") == []

    def test_returns_metas_from_index(self):
        source = WellKnownSkillSource()
        index = {
            "index_url": "https://example.com/.well-known/skills/index.json",
            "base_url": "https://example.com/.well-known/skills",
            "skills": [{"name": "plan", "description": "Plan things", "files": ["SKILL.md"]}],
        }
        with patch.object(source, "_parse_index", return_value=index):
            results = source.search("https://example.com/.well-known/skills/index.json")
        assert len(results) == 1
        assert results[0].name == "plan"
        assert results[0].identifier == "well-known:https://example.com/.well-known/skills/plan"

    def test_index_fetch_failure_returns_empty(self):
        source = WellKnownSkillSource()
        with patch.object(source, "_parse_index", return_value=None):
            assert source.search("https://example.com") == []


class TestInspect:

    def test_returns_none_for_unparseable_identifier(self):
        source = WellKnownSkillSource()
        assert source.inspect("not-a-url") is None

    def test_builds_meta_from_entry_and_skill_md(self):
        source = WellKnownSkillSource()
        entry = {"name": "plan", "description": "fallback desc", "files": ["SKILL.md"]}
        skill_md = "---\nname: plan\ndescription: real desc\n---\nbody"
        with patch.object(source, "_index_entry", return_value=entry), \
             patch.object(source, "_fetch_text", return_value=skill_md):
            meta = source.inspect("https://example.com/.well-known/skills/plan")
        assert meta.name == "plan"
        assert meta.description == "real desc"
        assert meta.source == "well-known"

    def test_returns_none_when_entry_missing(self):
        source = WellKnownSkillSource()
        with patch.object(source, "_index_entry", return_value=None):
            assert source.inspect("https://example.com/.well-known/skills/plan") is None

    def test_returns_none_when_skill_md_fetch_fails(self):
        source = WellKnownSkillSource()
        with patch.object(source, "_index_entry", return_value={"name": "plan"}), \
             patch.object(source, "_fetch_text", return_value=None):
            assert source.inspect("https://example.com/.well-known/skills/plan") is None


class TestFetch:

    def test_returns_none_for_unparseable_identifier(self):
        source = WellKnownSkillSource()
        assert source.fetch("not-a-url") is None

    def test_downloads_all_declared_files(self):
        source = WellKnownSkillSource()
        entry = {"name": "plan", "files": ["SKILL.md", "scripts/run.sh"]}
        texts = {
            "https://example.com/.well-known/skills/plan/SKILL.md": "---\nname: plan\n---\nbody",
            "https://example.com/.well-known/skills/plan/scripts/run.sh": "echo hi",
        }
        with patch.object(source, "_index_entry", return_value=entry), \
             patch.object(source, "_fetch_text", side_effect=lambda url: texts[url]):
            bundle = source.fetch("https://example.com/.well-known/skills/plan")
        assert bundle is not None
        assert bundle.name == "plan"
        assert set(bundle.files) == {"SKILL.md", "scripts/run.sh"}

    def test_rejects_unsafe_file_path_from_index(self):
        source = WellKnownSkillSource()
        entry = {"name": "plan", "files": ["../../etc/passwd"]}
        with patch.object(source, "_index_entry", return_value=entry):
            assert source.fetch("https://example.com/.well-known/skills/plan") is None

    def test_missing_skill_md_in_downloaded_files_returns_none(self):
        source = WellKnownSkillSource()
        entry = {"name": "plan", "files": ["other.txt"]}
        with patch.object(source, "_index_entry", return_value=entry), \
             patch.object(source, "_fetch_text", return_value="content"):
            assert source.fetch("https://example.com/.well-known/skills/plan") is None

    def test_returns_none_when_a_file_fetch_fails(self):
        source = WellKnownSkillSource()
        entry = {"name": "plan", "files": ["SKILL.md", "missing.txt"]}

        def fake_fetch_text(url):
            return None if "missing.txt" in url else "---\nname: plan\n---\nbody"

        with patch.object(source, "_index_entry", return_value=entry), \
             patch.object(source, "_fetch_text", side_effect=fake_fetch_text):
            assert source.fetch("https://example.com/.well-known/skills/plan") is None
