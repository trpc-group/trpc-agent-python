# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.skills.hub._clawhub.

Covers:
- source_id
- _normalize_tags: list vs dict-of-version-tags vs other
- _coerce_skill_payload: nested "skill" payload merging
- _search_score: exact / prefix / substring / term-overlap scoring
- _dedupe_results: case-insensitive de-dup by identifier
- inspect: builds SkillMeta from the skill endpoint
- fetch: resolves latest version then downloads a ZIP bundle
- _extract_files: dict-of-files, list-of-file-meta with inline/raw content
"""

from __future__ import annotations

import io
import zipfile
from unittest.mock import MagicMock
from unittest.mock import patch

import httpx
import pytest

from trpc_agent_sdk.skills.hub import ClawHubSource
from trpc_agent_sdk.skills.hub import SkillMeta


def _resp(status_code=200, json_data=None, content=b""):
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.content = content
    resp.headers = {}
    return resp


class TestSourceId:

    def test_source_id(self):
        assert ClawHubSource().source_id() == "clawhub"


class TestNormalizeTags:

    def test_list_tags(self):
        assert ClawHubSource._normalize_tags(["a", "b"]) == ["a", "b"]

    def test_dict_tags_excludes_latest(self):
        assert set(ClawHubSource._normalize_tags({"latest": "1.0", "1.0": "1.0"})) == {"1.0"}

    def test_other_type_returns_empty(self):
        assert ClawHubSource._normalize_tags(None) == []


class TestCoerceSkillPayload:

    def test_merges_nested_skill_payload(self):
        data = {"skill": {"name": "notion"}, "latestVersion": "1.2.0"}
        result = ClawHubSource._coerce_skill_payload(data)
        assert result == {"name": "notion", "latestVersion": "1.2.0"}

    def test_returns_dict_as_is_when_no_nested_skill(self):
        data = {"name": "notion"}
        assert ClawHubSource._coerce_skill_payload(data) == data

    def test_non_dict_returns_none(self):
        assert ClawHubSource._coerce_skill_payload(["not", "a", "dict"]) is None


class TestSearchScore:

    def test_exact_identifier_match_scores_highest(self):
        exact = SkillMeta(name="notion", description="", source="clawhub", identifier="notion")
        prefix = SkillMeta(name="notion-helper", description="", source="clawhub", identifier="notion-helper")
        exact_score = ClawHubSource._search_score("notion", exact)
        prefix_score = ClawHubSource._search_score("notion", prefix)
        assert exact_score > prefix_score > 0

    def test_unrelated_substring_scores_lower_than_prefix_match(self):
        prefix = SkillMeta(name="notion-helper", description="", source="clawhub", identifier="notion-helper")
        substring = SkillMeta(name="pro-notion-x", description="", source="clawhub", identifier="pro-notion-x")
        prefix_score = ClawHubSource._search_score("notion", prefix)
        substring_score = ClawHubSource._search_score("notion", substring)
        assert prefix_score > substring_score > 0

    def test_no_match_scores_zero(self):
        meta = SkillMeta(name="docx", description="word docs", source="clawhub", identifier="docx")
        assert ClawHubSource._search_score("notion", meta) == 0

    def test_empty_query_scores_one(self):
        meta = SkillMeta(name="docx", description="", source="clawhub", identifier="docx")
        assert ClawHubSource._search_score("", meta) == 1


class TestDedupeResults:

    def test_dedupes_case_insensitively_by_identifier(self):
        a = SkillMeta(name="Notion", description="", source="clawhub", identifier="notion")
        b = SkillMeta(name="notion again", description="", source="clawhub", identifier="Notion")
        assert len(ClawHubSource._dedupe_results([a, b])) == 1


class TestInspect:

    def test_builds_meta(self):
        source = ClawHubSource()
        data = {"displayName": "Notion", "summary": "Notion integration", "slug": "notion", "tags": ["productivity"]}
        with patch.object(source, "_get_json", return_value=data):
            meta = source.inspect("notion")
        assert meta.name == "Notion"
        assert meta.description == "Notion integration"
        assert meta.source == "clawhub"
        assert meta.identifier == "notion"
        assert meta.tags == ["productivity"]

    def test_returns_none_when_no_data(self):
        source = ClawHubSource()
        with patch.object(source, "_get_json", return_value=None):
            assert source.inspect("notion") is None


class TestFetch:

    def test_returns_none_when_skill_data_missing(self):
        source = ClawHubSource()
        with patch.object(source, "_get_json", return_value=None):
            assert source.fetch("notion") is None

    def test_returns_none_when_version_unresolvable(self):
        source = ClawHubSource()
        with patch.object(source, "_get_json", return_value={"slug": "notion"}), \
             patch.object(source, "_resolve_latest_version", return_value=None):
            assert source.fetch("notion") is None

    def test_downloads_zip_bundle(self):
        source = ClawHubSource()
        with patch.object(source, "_get_json", return_value={"slug": "notion"}), \
             patch.object(source, "_resolve_latest_version", return_value="1.0.0"), \
             patch.object(source, "_download_zip", return_value={"SKILL.md": "body"}):
            bundle = source.fetch("notion")
        assert bundle is not None
        assert bundle.name == "notion"
        assert bundle.files == {"SKILL.md": "body"}
        assert bundle.source == "clawhub"

    def test_falls_back_to_version_metadata_when_zip_incomplete(self):
        source = ClawHubSource()
        version_data = {"files": {"SKILL.md": "from version metadata"}}
        with patch.object(source, "_get_json", side_effect=[{"slug": "notion"}, version_data]), \
             patch.object(source, "_resolve_latest_version", return_value="1.0.0"), \
             patch.object(source, "_download_zip", return_value={}):
            bundle = source.fetch("notion")
        assert bundle is not None
        assert bundle.files == {"SKILL.md": "from version metadata"}

    def test_returns_none_when_no_skill_md_anywhere(self):
        source = ClawHubSource()
        with patch.object(source, "_get_json", side_effect=[{"slug": "notion"}, {"files": {}}]), \
             patch.object(source, "_resolve_latest_version", return_value="1.0.0"), \
             patch.object(source, "_download_zip", return_value={}):
            assert source.fetch("notion") is None


class TestExtractFiles:

    def test_dict_file_list(self):
        source = ClawHubSource()
        result = source._extract_files({"files": {"SKILL.md": "body", "bad": 1}})
        assert result == {"SKILL.md": "body"}

    def test_list_file_meta_with_inline_content(self):
        source = ClawHubSource()
        result = source._extract_files({"files": [{"path": "SKILL.md", "content": "body"}]})
        assert result == {"SKILL.md": "body"}

    def test_list_file_meta_with_raw_url(self):
        source = ClawHubSource()
        with patch.object(source, "_fetch_text", return_value="fetched body"):
            result = source._extract_files({"files": [{"name": "SKILL.md", "rawUrl": "https://example.com/SKILL.md"}]})
        assert result == {"SKILL.md": "fetched body"}

    def test_no_files_key_returns_empty(self):
        source = ClawHubSource()
        assert source._extract_files({}) == {}


class TestDownloadZip:

    def _make_zip_bytes(self, files: dict[str, bytes]) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, content in files.items():
                zf.writestr(name, content)
        return buf.getvalue()

    def test_extracts_text_files_from_zip(self):
        source = ClawHubSource()
        zip_bytes = self._make_zip_bytes({"SKILL.md": b"body", "scripts/run.sh": b"echo hi"})
        with patch("trpc_agent_sdk.skills.hub._clawhub.httpx.get", return_value=_resp(content=zip_bytes)):
            files = source._download_zip("notion", "1.0.0")
        assert files == {"SKILL.md": "body", "scripts/run.sh": "echo hi"}

    def test_rejects_unsafe_zip_member_paths(self):
        source = ClawHubSource()
        zip_bytes = self._make_zip_bytes({"SKILL.md": b"body", "../../evil.sh": b"rm -rf /"})
        with patch("trpc_agent_sdk.skills.hub._clawhub.httpx.get", return_value=_resp(content=zip_bytes)):
            files = source._download_zip("notion", "1.0.0")
        assert files == {"SKILL.md": "body"}

    def test_returns_empty_on_non_200(self):
        source = ClawHubSource()
        with patch("trpc_agent_sdk.skills.hub._clawhub.httpx.get", return_value=_resp(status_code=404)):
            assert source._download_zip("notion", "1.0.0") == {}

    def test_returns_empty_on_bad_zip(self):
        source = ClawHubSource()
        with patch("trpc_agent_sdk.skills.hub._clawhub.httpx.get", return_value=_resp(content=b"not a zip")):
            assert source._download_zip("notion", "1.0.0") == {}
