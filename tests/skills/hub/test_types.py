# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.skills.hub._types.

Covers:
- SkillMeta / SkillBundle dataclass defaults
- validate_skill_name / validate_category_name: single-segment-only paths
- validate_bundle_rel_path: nested paths allowed
- Path traversal / absolute-path / Windows-drive / non-string rejection
"""

from __future__ import annotations

import pytest

from trpc_agent_sdk.skills.hub import SkillBundle
from trpc_agent_sdk.skills.hub import SkillMeta
from trpc_agent_sdk.skills.hub import validate_bundle_rel_path
from trpc_agent_sdk.skills.hub import validate_category_name
from trpc_agent_sdk.skills.hub import validate_skill_name


class TestSkillMeta:

    def test_required_fields(self):
        meta = SkillMeta(name="plan", description="desc", source="github", identifier="owner/repo/plan")
        assert meta.name == "plan"
        assert meta.description == "desc"
        assert meta.source == "github"
        assert meta.identifier == "owner/repo/plan"

    def test_defaults(self):
        meta = SkillMeta(name="plan", description="desc", source="github", identifier="owner/repo/plan")
        assert meta.repo is None
        assert meta.path is None
        assert meta.tags == []
        assert meta.extra == {}

    def test_default_collections_are_independent_per_instance(self):
        a = SkillMeta(name="a", description="", source="s", identifier="a")
        b = SkillMeta(name="b", description="", source="s", identifier="b")
        a.tags.append("x")
        a.extra["k"] = "v"
        assert b.tags == []
        assert b.extra == {}


class TestSkillBundle:

    def test_required_fields(self):
        bundle = SkillBundle(
            name="plan",
            files={"SKILL.md": "---\nname: plan\n---\nbody"},
            source="github",
            identifier="owner/repo/plan",
        )
        assert bundle.name == "plan"
        assert bundle.files["SKILL.md"].startswith("---")
        assert bundle.metadata == {}

    def test_files_can_hold_bytes(self):
        bundle = SkillBundle(
            name="plan",
            files={
                "SKILL.md": "text",
                "logo.png": b"\x89PNG"
            },
            source="github",
            identifier="id",
        )
        assert isinstance(bundle.files["logo.png"], bytes)

    def test_default_metadata_independent_per_instance(self):
        a = SkillBundle(name="a", files={}, source="s", identifier="a")
        b = SkillBundle(name="b", files={}, source="s", identifier="b")
        a.metadata["category"] = "dev"
        assert b.metadata == {}


class TestValidateSkillName:

    @pytest.mark.parametrize("name", ["plan", "skill-creator", "skill_1", "a"])
    def test_valid_single_segment_names(self, name):
        assert validate_skill_name(name) == name

    def test_strips_whitespace(self):
        assert validate_skill_name("  plan  ") == "plan"

    @pytest.mark.parametrize(
        "name",
        [
            "",
            "   ",
            "/plan",
            "../plan",
            "a/b",
            "a/../b",
            "C:",
        ],
    )
    def test_rejects_unsafe_names(self, name):
        with pytest.raises(ValueError):
            validate_skill_name(name)

    def test_rejects_non_string(self):
        with pytest.raises(ValueError):
            validate_skill_name(123)  # type: ignore[arg-type]


class TestValidateCategoryName:

    def test_valid_single_segment_category(self):
        assert validate_category_name("hub") == "hub"

    def test_rejects_nested_category(self):
        with pytest.raises(ValueError):
            validate_category_name("hub/sub")

    def test_rejects_traversal(self):
        with pytest.raises(ValueError):
            validate_category_name("..")


class TestValidateBundleRelPath:

    def test_allows_nested_paths(self):
        assert validate_bundle_rel_path("scripts/run.sh") == "scripts/run.sh"

    def test_allows_single_segment(self):
        assert validate_bundle_rel_path("SKILL.md") == "SKILL.md"

    def test_normalizes_backslashes_to_forward_slashes(self):
        assert validate_bundle_rel_path("scripts\\run.sh") == "scripts/run.sh"

    def test_collapses_dot_segments(self):
        assert validate_bundle_rel_path("scripts/./run.sh") == "scripts/run.sh"

    @pytest.mark.parametrize(
        "rel_path",
        [
            "",
            "   ",
            "/etc/passwd",
            "../secret",
            "scripts/../../secret",
            "C:/Windows/System32",
        ],
    )
    def test_rejects_unsafe_paths(self, rel_path):
        with pytest.raises(ValueError):
            validate_bundle_rel_path(rel_path)

    def test_rejects_non_string(self):
        with pytest.raises(ValueError):
            validate_bundle_rel_path(None)  # type: ignore[arg-type]
