# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.skills._types.

Covers:
- parse_datetime / format_datetime helpers
- SkillRequires, SkillFrontMatter, SkillConfig, SkillSummary, SkillResource defaults
- Skill model construction
- SkillMetadata: from_dict, to_dict, round-trip
- SkillWorkspaceInputRecord: from_dict, to_dict
- SkillWorkspaceOutputRecord: from_dict, to_dict
- SkillWorkspaceMetadata: from_dict, to_dict, nested parsing
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from trpc_agent_sdk.skills._types import (
    Skill,
    SkillConfig,
    SkillFrontMatter,
    SkillMetadata,
    SkillRequires,
    SkillResource,
    SkillSummary,
    SkillWorkspaceInputRecord,
    SkillWorkspaceMetadata,
    SkillWorkspaceOutputRecord,
    format_datetime,
    parse_datetime,
)


# ---------------------------------------------------------------------------
# parse_datetime
# ---------------------------------------------------------------------------

class TestParseDatetime:
    def test_parse_none_returns_now(self):
        result = parse_datetime(None)
        assert isinstance(result, datetime)

    def test_parse_empty_string_returns_now(self):
        result = parse_datetime("")
        assert isinstance(result, datetime)

    def test_parse_iso_string(self):
        dt = parse_datetime("2025-06-15T10:30:00")
        assert dt.year == 2025
        assert dt.month == 6
        assert dt.day == 15
        assert dt.hour == 10

    def test_parse_datetime_object(self):
        original = datetime(2025, 1, 1, 12, 0, 0)
        result = parse_datetime(original)
        assert result is original

    def test_parse_int_returns_now(self):
        result = parse_datetime(12345)
        assert isinstance(result, datetime)

    def test_parse_false_returns_now(self):
        result = parse_datetime(False)
        assert isinstance(result, datetime)


# ---------------------------------------------------------------------------
# format_datetime
# ---------------------------------------------------------------------------

class TestFormatDatetime:
    def test_format_none_returns_iso_string(self):
        result = format_datetime(None)
        assert isinstance(result, str)
        datetime.fromisoformat(result)

    def test_format_datetime(self):
        dt = datetime(2025, 6, 15, 10, 30, 0)
        result = format_datetime(dt)
        assert "2025-06-15" in result
        assert "10:30:00" in result


# ---------------------------------------------------------------------------
# Pydantic model defaults
# ---------------------------------------------------------------------------

class TestSkillRequires:
    def test_defaults(self):
        r = SkillRequires()
        assert r.bins == []
        assert r.any_bins == []
        assert r.env == []
        assert r.config == []
        assert r.install == []


class TestSkillFrontMatter:
    def test_defaults(self):
        fm = SkillFrontMatter()
        assert fm.skill_key == ""
        assert fm.primary_env == ""
        assert fm.emoji == ""
        assert fm.homepage == ""
        assert fm.always is False
        assert fm.os == []
        assert isinstance(fm.requires, SkillRequires)

    def test_with_values(self):
        fm = SkillFrontMatter(
            skill_key="test-key",
            primary_env="API_KEY",
            emoji="🧪",
            always=True,
            os=["linux", "darwin"],
        )
        assert fm.skill_key == "test-key"
        assert fm.always is True
        assert len(fm.os) == 2


class TestSkillConfig:
    def test_defaults(self):
        c = SkillConfig()
        assert c.enabled is None
        assert c.api_key == ""
        assert c.env == {}


class TestSkillSummary:
    def test_defaults(self):
        s = SkillSummary()
        assert s.name == ""
        assert s.description == ""

    def test_with_values(self):
        s = SkillSummary(name="test", description="desc")
        assert s.name == "test"


class TestSkillResource:
    def test_creation(self):
        r = SkillResource(path="docs/readme.md", content="# Hello")
        assert r.path == "docs/readme.md"
        assert r.content == "# Hello"


class TestSkill:
    def test_defaults(self):
        s = Skill()
        assert s.body == ""
        assert s.resources == []
        assert s.tools == []
        assert s.base_dir == ""
        assert isinstance(s.summary, SkillSummary)

    def test_with_values(self):
        s = Skill(
            summary=SkillSummary(name="test", description="Test skill"),
            body="# Test Body",
            tools=["tool1", "tool2"],
            base_dir="/path/to/skill",
            resources=[SkillResource(path="doc.md", content="doc")],
        )
        assert s.summary.name == "test"
        assert len(s.tools) == 2
        assert len(s.resources) == 1


# ---------------------------------------------------------------------------
# SkillMetadata
# ---------------------------------------------------------------------------

class TestSkillMetadata:
    def test_defaults(self):
        m = SkillMetadata()
        assert m.name == ""
        assert m.rel_path == ""
        assert m.digest == ""
        assert m.mounted is False
        assert m.staged_at is None

    def test_from_dict_full(self):
        data = {
            "name": "test-skill",
            "rel_path": "skills/test",
            "digest": "abc123",
            "mounted": True,
            "staged_at": "2025-06-15T10:30:00",
        }
        m = SkillMetadata.from_dict(data)
        assert m.name == "test-skill"
        assert m.rel_path == "skills/test"
        assert m.digest == "abc123"
        assert m.mounted is True
        assert m.staged_at.year == 2025

    def test_from_dict_empty(self):
        m = SkillMetadata.from_dict({})
        assert m.name == ""
        assert m.mounted is False

    def test_to_dict(self):
        m = SkillMetadata(
            name="test",
            rel_path="skills/test",
            digest="abc",
            mounted=True,
            staged_at=datetime(2025, 6, 15),
        )
        d = m.to_dict()
        assert d["name"] == "test"
        assert d["rel_path"] == "skills/test"
        assert d["digest"] == "abc"
        assert d["mounted"] is True
        assert "2025-06-15" in d["staged_at"]

    def test_round_trip(self):
        original = SkillMetadata(
            name="round-trip",
            rel_path="skills/rt",
            digest="hash",
            mounted=True,
            staged_at=datetime(2025, 1, 1, 12, 0, 0),
        )
        d = original.to_dict()
        restored = SkillMetadata.from_dict(d)
        assert restored.name == original.name
        assert restored.digest == original.digest
        assert restored.mounted == original.mounted


# ---------------------------------------------------------------------------
# SkillWorkspaceInputRecord
# ---------------------------------------------------------------------------

class TestSkillWorkspaceInputRecord:
    def test_from_dict_full(self):
        data = {
            "src": "/src/path",
            "dst": "/dst/path",
            "timestamp": "2025-06-15T10:00:00",
            "resolved": "/resolved",
            "version": 3,
            "mode": "copy",
        }
        r = SkillWorkspaceInputRecord.from_dict(data)
        assert r.src == "/src/path"
        assert r.dst == "/dst/path"
        assert r.version == 3
        assert r.mode == "copy"

    def test_from_dict_empty(self):
        r = SkillWorkspaceInputRecord.from_dict({})
        assert r.src == ""
        assert r.version == 0

    def test_to_dict(self):
        r = SkillWorkspaceInputRecord(
            src="s", dst="d", resolved="r", version=1, mode="link",
            timestamp=datetime(2025, 1, 1),
        )
        d = r.to_dict()
        assert d["src"] == "s"
        assert d["dst"] == "d"
        assert d["version"] == 1

    def test_round_trip(self):
        original = SkillWorkspaceInputRecord(src="a", dst="b", version=5)
        d = original.to_dict()
        restored = SkillWorkspaceInputRecord.from_dict(d)
        assert restored.src == original.src
        assert restored.version == original.version


# ---------------------------------------------------------------------------
# SkillWorkspaceOutputRecord
# ---------------------------------------------------------------------------

class TestSkillWorkspaceOutputRecord:
    def test_from_dict_full(self):
        data = {
            "globs": ["*.txt", "*.md"],
            "limits_hit": 2,
            "timestamp": "2025-06-15T10:00:00",
            "saved_as": ["out/a.txt"],
            "versions": [1, 2],
        }
        r = SkillWorkspaceOutputRecord.from_dict(data)
        assert r.globs == ["*.txt", "*.md"]
        assert r.limits_hit == 2
        assert r.saved_as == ["out/a.txt"]
        assert r.versions == [1, 2]

    def test_from_dict_empty(self):
        r = SkillWorkspaceOutputRecord.from_dict({})
        assert r.globs == []
        assert r.limits_hit == 0

    def test_to_dict(self):
        r = SkillWorkspaceOutputRecord(
            globs=["*.py"],
            limits_hit=1,
            saved_as=["out/test.py"],
            versions=[3],
            timestamp=datetime(2025, 1, 1),
        )
        d = r.to_dict()
        assert d["globs"] == ["*.py"]
        assert d["limits_hit"] == 1
        assert d["saved_as"] == ["out/test.py"]
        assert d["versions"] == [3]


# ---------------------------------------------------------------------------
# SkillWorkspaceMetadata
# ---------------------------------------------------------------------------

class TestSkillWorkspaceMetadata:
    def test_defaults(self):
        m = SkillWorkspaceMetadata()
        assert m.version == 0
        assert m.skills == {}
        assert m.inputs == []
        assert m.outputs == []

    def test_from_dict_full(self):
        data = {
            "version": 2,
            "created_at": "2025-01-01T00:00:00",
            "updated_at": "2025-06-15T00:00:00",
            "last_access": "2025-06-15T12:00:00",
            "skills": {
                "weather": {
                    "name": "weather",
                    "rel_path": "skills/weather",
                    "digest": "abc",
                    "mounted": True,
                    "staged_at": "2025-06-15T00:00:00",
                },
            },
            "inputs": [
                {"src": "s", "dst": "d", "version": 1},
            ],
            "outputs": [
                {"globs": ["*.txt"], "limits_hit": 0},
            ],
        }
        m = SkillWorkspaceMetadata.from_dict(data)
        assert m.version == 2
        assert "weather" in m.skills
        assert m.skills["weather"].name == "weather"
        assert len(m.inputs) == 1
        assert len(m.outputs) == 1

    def test_from_dict_empty(self):
        m = SkillWorkspaceMetadata.from_dict({})
        assert m.version == 1
        assert m.skills == {}

    def test_to_dict(self):
        m = SkillWorkspaceMetadata(
            version=2,
            created_at=datetime(2025, 1, 1),
            updated_at=datetime(2025, 6, 15),
            last_access=datetime(2025, 6, 15),
        )
        m.skills["test"] = SkillMetadata(name="test", digest="hash")
        d = m.to_dict()
        assert d["version"] == 2
        assert "test" in d["skills"]

    def test_round_trip(self):
        m = SkillWorkspaceMetadata(version=3)
        m.skills["s1"] = SkillMetadata(name="s1", digest="d1", mounted=True)
        m.inputs.append(SkillWorkspaceInputRecord(src="a", dst="b"))
        m.outputs.append(SkillWorkspaceOutputRecord(globs=["*.txt"]))

        d = m.to_dict()
        restored = SkillWorkspaceMetadata.from_dict(d)
        assert restored.version == 3
        assert "s1" in restored.skills
        assert len(restored.inputs) == 1
        assert len(restored.outputs) == 1
