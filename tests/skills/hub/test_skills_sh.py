# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.skills.hub._skills_sh.

Covers:
- source_id
- _normalize_identifier: alias-prefix stripping
- _candidate_identifiers: standard skill-path candidates, deduped
- _wrap_identifier
- _token_variants: slug/case/underscore normalization used for fuzzy matching
- fetch: resolves via first matching candidate through the underlying GitHubSource
- inspect: delegates to GitHub inspect via candidates, then discovery fallback
"""

from __future__ import annotations

from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from trpc_agent_sdk.skills.hub import GitHubAuth
from trpc_agent_sdk.skills.hub import SkillBundle
from trpc_agent_sdk.skills.hub import SkillMeta
from trpc_agent_sdk.skills.hub._skills_sh import SkillsShSource


class TestSourceId:

    def test_source_id(self):
        assert SkillsShSource(GitHubAuth()).source_id() == "skills-sh"


class TestNormalizeIdentifier:

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("skills-sh/owner/repo/plan", "owner/repo/plan"),
            ("skills.sh/owner/repo/plan", "owner/repo/plan"),
            ("owner/repo/plan", "owner/repo/plan"),
        ],
    )
    def test_strips_known_prefixes(self, raw, expected):
        assert SkillsShSource._normalize_identifier(raw) == expected


class TestCandidateIdentifiers:

    def test_generates_standard_paths(self):
        candidates = SkillsShSource._candidate_identifiers("owner/repo/plan")
        assert candidates == [
            "owner/repo/plan",
            "owner/repo/skills/plan",
            "owner/repo/.agents/skills/plan",
            "owner/repo/.claude/skills/plan",
        ]

    def test_short_identifier_returned_as_is(self):
        assert SkillsShSource._candidate_identifiers("owner/repo") == ["owner/repo"]

    def test_dedupes_when_skill_path_already_prefixed(self):
        candidates = SkillsShSource._candidate_identifiers("owner/repo/skills/plan")
        assert candidates.count("owner/repo/skills/plan") == 1


class TestWrapIdentifier:

    def test_wraps_with_prefix(self):
        assert SkillsShSource._wrap_identifier("owner/repo/plan") == "skills-sh/owner/repo/plan"


class TestTokenVariants:

    def test_generates_case_and_separator_variants(self):
        variants = SkillsShSource._token_variants("Skill_Creator")
        assert "skill_creator" in variants
        assert "skill-creator" in variants

    def test_empty_value_returns_empty_set(self):
        assert SkillsShSource._token_variants(None) == set()
        assert SkillsShSource._token_variants("") == set()

    def test_strips_html_tags(self):
        variants = SkillsShSource._token_variants("<b>plan</b>")
        assert "plan" in variants


class TestMatchesSkillTokens:

    def test_matches_on_name(self):
        meta = SkillMeta(name="plan", description="", source="github", identifier="owner/repo/skills/plan")
        assert SkillsShSource._matches_skill_tokens(meta, ["plan"]) is True

    def test_no_match(self):
        meta = SkillMeta(name="plan", description="", source="github", identifier="owner/repo/skills/plan")
        assert SkillsShSource._matches_skill_tokens(meta, ["docx"]) is False


class TestFetch:

    def test_returns_bundle_from_first_matching_candidate(self):
        source = SkillsShSource(GitHubAuth())
        bundle = SkillBundle(name="plan", files={"SKILL.md": "body"}, source="github", identifier="owner/repo/plan")
        with patch.object(source, "_fetch_detail_page", return_value=None), \
             patch.object(source.github, "fetch", return_value=bundle) as fetch_mock:
            result = source.fetch("owner/repo/plan")
        assert result is bundle
        assert result.source == "skills.sh"
        assert result.identifier == "skills-sh/owner/repo/plan"
        fetch_mock.assert_called_once_with("owner/repo/plan")

    def test_falls_back_to_discovery_when_no_candidate_matches(self):
        source = SkillsShSource(GitHubAuth())
        bundle = SkillBundle(name="plan",
                             files={"SKILL.md": "body"},
                             source="github",
                             identifier="owner/repo/other/plan")
        with patch.object(source, "_fetch_detail_page", return_value=None), \
             patch.object(source.github, "fetch", side_effect=[None, None, None, None, bundle]), \
             patch.object(source, "_discover_identifier", return_value="owner/repo/other/plan"):
            result = source.fetch("owner/repo/plan")
        assert result is bundle

    def test_returns_none_when_nothing_resolves(self):
        source = SkillsShSource(GitHubAuth())
        with patch.object(source, "_fetch_detail_page", return_value=None), \
             patch.object(source.github, "fetch", return_value=None), \
             patch.object(source, "_discover_identifier", return_value=None):
            assert source.fetch("owner/repo/plan") is None


class TestInspect:

    def test_returns_meta_from_candidate(self):
        source = SkillsShSource(GitHubAuth())
        meta = SkillMeta(name="plan", description="", source="github", identifier="owner/repo/plan")
        with patch.object(source, "_fetch_detail_page", return_value=None), \
             patch.object(source.github, "inspect", return_value=meta):
            result = source.inspect("owner/repo/plan")
        assert result is not None
        assert result.source == "skills.sh"
        assert result.identifier == "skills-sh/owner/repo/plan"

    def test_returns_none_when_unresolvable(self):
        source = SkillsShSource(GitHubAuth())
        with patch.object(source, "_fetch_detail_page", return_value=None), \
             patch.object(source.github, "inspect", return_value=None), \
             patch.object(source, "_discover_identifier", return_value=None):
            assert source.inspect("owner/repo/plan") is None
