# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

from datetime import datetime
from pathlib import Path

import pytest
from trpc_agent_sdk.skills import Skill
from trpc_agent_sdk.skills import SkillMetadata
from trpc_agent_sdk.skills import SkillResource
from trpc_agent_sdk.skills import SkillSummary
from trpc_agent_sdk.skills import SkillWorkspaceInputRecord
from trpc_agent_sdk.skills import SkillWorkspaceMetadata
from trpc_agent_sdk.skills import SkillWorkspaceOutputRecord
from trpc_agent_sdk.skills import format_datetime
from trpc_agent_sdk.skills import parse_datetime


class TestParseDatetime:
    """Test suite for parse_datetime function."""

    def test_parse_datetime_from_string(self):
        """Test parsing datetime from ISO format string."""
        dt_str = "2024-01-01T12:00:00"
        result = parse_datetime(dt_str)

        assert isinstance(result, datetime)
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 1

    def test_parse_datetime_from_datetime(self):
        """Test parsing datetime from datetime object."""
        dt = datetime(2024, 1, 1, 12, 0, 0)
        result = parse_datetime(dt)

        assert result == dt

    def test_parse_datetime_none(self):
        """Test parsing datetime from None returns current time."""
        result = parse_datetime(None)

        assert isinstance(result, datetime)

    def test_parse_datetime_empty_string(self):
        """Test parsing datetime from empty string returns current time."""
        result = parse_datetime("")

        assert isinstance(result, datetime)

    def test_parse_datetime_invalid_type(self):
        """Test parsing datetime from invalid type returns current time."""
        result = parse_datetime(123)

        assert isinstance(result, datetime)


class TestFormatDatetime:
    """Test suite for format_datetime function."""

    def test_format_datetime(self):
        """Test formatting datetime to ISO string."""
        dt = datetime(2024, 1, 1, 12, 0, 0)
        result = format_datetime(dt)

        assert isinstance(result, str)
        assert "2024-01-01" in result

    def test_format_datetime_none(self):
        """Test formatting None datetime returns current time ISO string."""
        result = format_datetime(None)

        assert isinstance(result, str)
        assert isinstance(datetime.fromisoformat(result), datetime)


class TestSkillSummary:
    """Test suite for SkillSummary class."""

    def test_create_skill_summary(self):
        """Test creating a skill summary."""
        summary = SkillSummary(name="test-skill", description="Test skill description")

        assert summary.name == "test-skill"
        assert summary.description == "Test skill description"

    def test_create_skill_summary_defaults(self):
        """Test creating skill summary with defaults."""
        summary = SkillSummary()

        assert summary.name == ""
        assert summary.description == ""


class TestSkillResource:
    """Test suite for SkillResource class."""

    def test_create_skill_resource(self):
        """Test creating a skill resource."""
        resource = SkillResource(path="data/file.txt", content="file content")

        assert resource.path == "data/file.txt"
        assert resource.content == "file content"

    def test_create_skill_resource_required_fields(self):
        """Test that SkillResource requires all fields."""
        with pytest.raises(Exception):  # Pydantic validation error
            SkillResource(path="data/file.txt")


class TestSkill:
    """Test suite for Skill class."""

    def test_create_skill(self):
        """Test creating a skill."""
        summary = SkillSummary(name="test-skill", description="Test")
        resource = SkillResource(path="data.txt", content="content")
        skill = Skill(
            path=Path("/path/to/skill"),
            summary=summary,
            body="skill body",
            resources=[resource]
        )

        assert skill.path == Path("/path/to/skill")
        assert skill.summary == summary
        assert skill.body == "skill body"
        assert len(skill.resources) == 1

    def test_create_skill_defaults(self):
        """Test creating skill with defaults."""
        skill = Skill()

        assert skill.path == Path("")
        assert isinstance(skill.summary, SkillSummary)
        assert skill.body == ""
        assert skill.resources == []


class TestSkillMetadata:
    """Test suite for SkillMetadata class."""

    def test_create_skill_metadata(self):
        """Test creating skill metadata."""
        dt = datetime.now()
        metadata = SkillMetadata(
            name="test-skill",
            rel_path="skills/test",
            digest="abc123",
            mounted=True,
            staged_at=dt
        )

        assert metadata.name == "test-skill"
        assert metadata.rel_path == "skills/test"
        assert metadata.digest == "abc123"
        assert metadata.mounted is True
        assert metadata.staged_at == dt

    def test_create_skill_metadata_defaults(self):
        """Test creating skill metadata with defaults."""
        metadata = SkillMetadata()

        assert metadata.name == ""
        assert metadata.rel_path == ""
        assert metadata.digest == ""
        assert metadata.mounted is False
        assert metadata.staged_at is None

    def test_skill_metadata_from_dict(self):
        """Test creating skill metadata from dictionary."""
        data = {
            "name": "test-skill",
            "rel_path": "skills/test",
            "digest": "abc123",
            "mounted": True,
            "staged_at": "2024-01-01T12:00:00"
        }

        metadata = SkillMetadata.from_dict(data)

        assert metadata.name == "test-skill"
        assert metadata.rel_path == "skills/test"
        assert metadata.digest == "abc123"
        assert metadata.mounted is True
        assert isinstance(metadata.staged_at, datetime)

    def test_skill_metadata_to_dict(self):
        """Test converting skill metadata to dictionary."""
        dt = datetime(2024, 1, 1, 12, 0, 0)
        metadata = SkillMetadata(
            name="test-skill",
            rel_path="skills/test",
            digest="abc123",
            mounted=True,
            staged_at=dt
        )

        data = metadata.to_dict()

        assert data["name"] == "test-skill"
        assert data["rel_path"] == "skills/test"
        assert data["digest"] == "abc123"
        assert data["mounted"] is True
        assert isinstance(data["staged_at"], str)


class TestSkillWorkspaceInputRecord:
    """Test suite for SkillWorkspaceInputRecord class."""

    def test_create_input_record(self):
        """Test creating input record."""
        dt = datetime.now()
        record = SkillWorkspaceInputRecord(
            src="artifact://name",
            dst="/tmp/input",
            timestamp=dt,
            resolved="/resolved/path",
            version=1,
            mode="copy"
        )

        assert record.src == "artifact://name"
        assert record.dst == "/tmp/input"
        assert record.timestamp == dt
        assert record.resolved == "/resolved/path"
        assert record.version == 1
        assert record.mode == "copy"

    def test_create_input_record_defaults(self):
        """Test creating input record with defaults."""
        record = SkillWorkspaceInputRecord()

        assert record.src == ""
        assert record.dst == ""
        assert record.timestamp is None
        assert record.resolved == ""
        assert record.version == 0
        assert record.mode == ""

    def test_input_record_from_dict(self):
        """Test creating input record from dictionary."""
        data = {
            "src": "artifact://name",
            "dst": "/tmp/input",
            "timestamp": "2024-01-01T12:00:00",
            "resolved": "/resolved/path",
            "version": 1,
            "mode": "copy"
        }

        record = SkillWorkspaceInputRecord.from_dict(data)

        assert record.src == "artifact://name"
        assert record.dst == "/tmp/input"
        assert isinstance(record.timestamp, datetime)
        assert record.resolved == "/resolved/path"
        assert record.version == 1
        assert record.mode == "copy"

    def test_input_record_to_dict(self):
        """Test converting input record to dictionary."""
        dt = datetime(2024, 1, 1, 12, 0, 0)
        record = SkillWorkspaceInputRecord(
            src="artifact://name",
            dst="/tmp/input",
            timestamp=dt,
            resolved="/resolved/path",
            version=1,
            mode="copy"
        )

        data = record.to_dict()

        assert data["src"] == "artifact://name"
        assert data["dst"] == "/tmp/input"
        assert isinstance(data["timestamp"], str)
        assert data["resolved"] == "/resolved/path"
        assert data["version"] == 1
        assert data["mode"] == "copy"


class TestSkillWorkspaceOutputRecord:
    """Test suite for SkillWorkspaceOutputRecord class."""

    def test_create_output_record(self):
        """Test creating output record."""
        dt = datetime.now()
        record = SkillWorkspaceOutputRecord(
            globs=["out/*.txt", "out/*.json"],
            limits_hit=1,
            timestamp=dt,
            saved_as=["output.txt", "output.json"],
            versions=[1, 2]
        )

        assert record.globs == ["out/*.txt", "out/*.json"]
        assert record.limits_hit == 1
        assert record.timestamp == dt
        assert record.saved_as == ["output.txt", "output.json"]
        assert record.versions == [1, 2]

    def test_create_output_record_defaults(self):
        """Test creating output record with defaults."""
        record = SkillWorkspaceOutputRecord()

        assert record.globs == []
        assert record.limits_hit == 0
        assert record.timestamp is None
        assert record.saved_as == []
        assert record.versions == []

    def test_output_record_from_dict(self):
        """Test creating output record from dictionary."""
        data = {
            "globs": ["out/*.txt"],
            "limits_hit": 1,
            "timestamp": "2024-01-01T12:00:00",
            "saved_as": ["output.txt"],
            "versions": [1]
        }

        record = SkillWorkspaceOutputRecord.from_dict(data)

        assert record.globs == ["out/*.txt"]
        assert record.limits_hit == 1
        assert isinstance(record.timestamp, datetime)
        assert record.saved_as == ["output.txt"]
        assert record.versions == [1]

    def test_output_record_to_dict(self):
        """Test converting output record to dictionary."""
        dt = datetime(2024, 1, 1, 12, 0, 0)
        record = SkillWorkspaceOutputRecord(
            globs=["out/*.txt"],
            limits_hit=1,
            timestamp=dt,
            saved_as=["output.txt"],
            versions=[1]
        )

        data = record.to_dict()

        assert data["globs"] == ["out/*.txt"]
        assert data["limits_hit"] == 1
        assert isinstance(data["timestamp"], str)
        assert data["saved_as"] == ["output.txt"]
        assert data["versions"] == [1]


class TestSkillWorkspaceMetadata:
    """Test suite for SkillWorkspaceMetadata class."""

    def test_create_workspace_metadata(self):
        """Test creating workspace metadata."""
        created_at = datetime(2024, 1, 1, 12, 0, 0)
        updated_at = datetime(2024, 1, 2, 12, 0, 0)
        skill_meta = SkillMetadata(name="test-skill")
        input_rec = SkillWorkspaceInputRecord(src="input")
        output_rec = SkillWorkspaceOutputRecord(globs=["out/*"])

        metadata = SkillWorkspaceMetadata(
            version=1,
            created_at=created_at,
            updated_at=updated_at,
            skills={"test-skill": skill_meta},
            inputs=[input_rec],
            outputs=[output_rec]
        )

        assert metadata.version == 1
        assert metadata.created_at == created_at
        assert metadata.updated_at == updated_at
        assert len(metadata.skills) == 1
        assert len(metadata.inputs) == 1
        assert len(metadata.outputs) == 1

    def test_create_workspace_metadata_defaults(self):
        """Test creating workspace metadata with defaults."""
        metadata = SkillWorkspaceMetadata()

        assert metadata.version == 0
        assert metadata.created_at is None
        assert metadata.updated_at is None
        assert metadata.last_access is None
        assert metadata.skills == {}
        assert metadata.inputs == []
        assert metadata.outputs == []

    def test_workspace_metadata_from_dict(self):
        """Test creating workspace metadata from dictionary."""
        data = {
            "version": 1,
            "created_at": "2024-01-01T12:00:00",
            "updated_at": "2024-01-02T12:00:00",
            "last_access": "2024-01-03T12:00:00",
            "skills": {
                "test-skill": {
                    "name": "test-skill",
                    "rel_path": "skills/test",
                    "digest": "abc123",
                    "mounted": True
                }
            },
            "inputs": [{
                "src": "artifact://name",
                "dst": "/tmp/input"
            }],
            "outputs": [{
                "globs": ["out/*.txt"],
                "limits_hit": 0
            }]
        }

        metadata = SkillWorkspaceMetadata.from_dict(data)

        assert metadata.version == 1
        assert isinstance(metadata.created_at, datetime)
        assert isinstance(metadata.updated_at, datetime)
        assert isinstance(metadata.last_access, datetime)
        assert len(metadata.skills) == 1
        assert len(metadata.inputs) == 1
        assert len(metadata.outputs) == 1

    def test_workspace_metadata_to_dict(self):
        """Test converting workspace metadata to dictionary."""
        created_at = datetime(2024, 1, 1, 12, 0, 0)
        skill_meta = SkillMetadata(name="test-skill")
        input_rec = SkillWorkspaceInputRecord(src="input")
        output_rec = SkillWorkspaceOutputRecord(globs=["out/*"])

        metadata = SkillWorkspaceMetadata(
            version=1,
            created_at=created_at,
            skills={"test-skill": skill_meta},
            inputs=[input_rec],
            outputs=[output_rec]
        )

        data = metadata.to_dict()

        assert data["version"] == 1
        assert isinstance(data["created_at"], str)
        assert isinstance(data["updated_at"], str)
        assert len(data["skills"]) == 1
        assert len(data["inputs"]) == 1
        assert len(data["outputs"]) == 1

