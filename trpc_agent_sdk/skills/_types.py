# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""Types for TRPC Agent skills system.

This module defines types for the skills system.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from typing import Dict
from typing import Optional

from pydantic import BaseModel
from pydantic import Field


def parse_datetime(data: Any) -> datetime:
    """Parse a datetime from a string or datetime object."""
    if not data:
        return datetime.now()
    if isinstance(data, str):
        return datetime.fromisoformat(data)
    elif isinstance(data, datetime):
        return data
    else:
        return datetime.now()


def format_datetime(data: Optional[datetime]) -> str:
    """Format a datetime to a string."""
    if not data:
        return datetime.now().isoformat()
    return data.isoformat()


class SkillRequires(BaseModel):
    """Runtime requirements declared in SKILL.md frontmatter."""

    bins: list[str] = Field(default_factory=list, description="Binaries that must all be present on PATH")
    any_bins: list[str] = Field(default_factory=list, description="At least one of these binaries must be on PATH")
    env: list[str] = Field(default_factory=list, description="Environment variable names that must be set")
    config: list[str] = Field(default_factory=list, description="Config keys that must be available")
    install: list[str] = Field(default_factory=list, description="Install commands / hints (informational)")


class SkillFrontMatter(BaseModel):
    """Extended frontmatter fields parsed from SKILL.md (OpenClaw-compatible)."""

    skill_key: str = Field(default="", description="Alternative key used for config lookup; falls back to skill name")
    primary_env: str = Field(default="", description="Primary API-key env-var name (used when api_key is set)")
    emoji: str = Field(default="", description="Display emoji")
    homepage: str = Field(default="", description="Skill homepage URL")
    always: bool = Field(default=False, description="When True the skill is always eligible regardless of requirements")
    os: list[str] = Field(default_factory=list, description="Allowed OS identifiers (linux / darwin / windows)")
    requires: SkillRequires = Field(default_factory=SkillRequires, description="Runtime requirements")


class SkillConfig(BaseModel):
    """Per-skill configuration supplied by the host application."""

    enabled: Optional[bool] = Field(default=None, description="Explicit enable/disable override; None means unset")
    api_key: str = Field(default="", description="API key injected as the primary_env variable")
    env: dict[str, str] = Field(default_factory=dict, description="Extra environment variables for this skill")


class SkillSummary(BaseModel):
    """Minimal information for a skill (name and description only)."""

    name: str = Field(default="", description="Skill name")
    """ skill name"""

    description: str = Field(default="", description="Skill description")
    """ skill description"""


class SkillResource(BaseModel):
    """Represents an auxiliary resource of a skill."""

    path: str = Field(..., description="Relative path to the resource file")
    """ relative path to the resource file"""

    content: str = Field(..., description="Resource content")
    """ resource content"""


class Skill(BaseModel):
    """Full content of a skill including metadata, body, and resources."""

    summary: SkillSummary = Field(default_factory=SkillSummary, description="Skill summary")
    """ skill summary"""

    body: str = Field(default="", description="Skill body")
    """ skill body"""

    resources: list[SkillResource] = Field(default_factory=list, description="Skill resources")
    """ skill resources"""

    tools: list[str] = Field(default_factory=list, description="Tool names defined in SKILL.md")
    """ tool names extracted from Tools section in SKILL.md"""

    base_dir: str = Field(default="", description="Absolute path to the skill directory (set by repository)")
    """ absolute path populated by the repository; used for __BASE_DIR__ placeholder replacement"""


class SkillMetadata(BaseModel):
    """Metadata for a skill."""

    name: str = Field(default="", description="Skill name")
    """ skill name"""

    rel_path: str = Field(default="", description="Relative path to the skill directory")
    """ relative path to the skill directory"""

    digest: str = Field(default="", description="Digest of the skill")
    """ skill version"""

    mounted: bool = Field(default=False, description="Whether the skill is mounted")
    """ whether the skill is mounted"""

    staged_at: Optional[datetime] = Field(default=None, description="The timestamp when the skill was staged")
    """ timestamp when the skill was staged"""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'SkillMetadata':
        """Create SkillMetadata instance from dictionary."""
        return cls(
            name=data.get("name", ""),
            rel_path=data.get("rel_path", ""),
            digest=data.get("digest", ""),
            mounted=data.get("mounted", False),
            staged_at=parse_datetime(data.get("staged_at", None)),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert SkillMetadata instance to dictionary for JSON serialization."""
        return {
            "name": self.name,
            "rel_path": self.rel_path,
            "digest": self.digest,
            "mounted": self.mounted,
            "staged_at": format_datetime(self.staged_at)
        }


class SkillWorkspaceInputRecord(BaseModel):
    """Input record for a skill workspace."""

    src: str = Field(default="", description="Input name")
    """ input name"""

    dst: str = Field(default="", description="Input value")
    """ input value"""

    timestamp: Optional[datetime] = Field(default=None, description="The timestamp when the input was created")
    """ timestamp when the input was created"""

    resolved: str = Field(default="", description="Resolved input value")
    """ resolved input value"""

    version: int = Field(default=0, description="The version of the input")
    """ version of the input"""

    mode: str = Field(default="", description="The mode of the input")
    """ mode of the input"""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'SkillWorkspaceInputRecord':
        """Create SkillWorkspaceInputRecord instance from dictionary."""
        return cls(
            src=data.get("src", ""),
            dst=data.get("dst", ""),
            timestamp=parse_datetime(data.get("timestamp", None)),
            resolved=data.get("resolved", ""),
            version=data.get("version", 0),
            mode=data.get("mode", ""),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert SkillWorkspaceInputRecord instance to dictionary for JSON serialization."""
        return {
            "src": self.src,
            "dst": self.dst,
            "timestamp": format_datetime(self.timestamp),
            "resolved": self.resolved,
            "version": self.version,
            "mode": self.mode,
        }


class SkillWorkspaceOutputRecord(BaseModel):
    """Output record for a skill workspace."""

    globs: list[str] = Field(default_factory=list, description="Output name")
    """ output name"""

    limits_hit: int = Field(default=0, description="The number of times the output was hit")
    """ number of times the output was hit"""

    timestamp: Optional[datetime] = Field(default=None, description="The timestamp when the output was created")
    """ timestamp when the output was created"""

    saved_as: list[str] = Field(default_factory=list, description="The names of the output files")
    """ names of the output files"""

    versions: list[int] = Field(default_factory=list, description="The versions of the output")
    """ versions of the output"""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> 'SkillWorkspaceOutputRecord':
        """Create SkillWorkspaceOutputRecord instance from dictionary."""
        return cls(
            globs=data.get("globs", []),
            limits_hit=data.get("limits_hit", 0),
            timestamp=parse_datetime(data.get("timestamp", None)),
            saved_as=data.get("saved_as", []),
            versions=data.get("versions", []),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert SkillWorkspaceOutputRecord instance to dictionary for JSON serialization."""
        return {
            "globs": self.globs,
            "limits_hit": self.limits_hit,
            "timestamp": format_datetime(self.timestamp),
            "saved_as": [Path(path).as_posix() for path in self.saved_as],
            "versions": [version for version in self.versions],
        }


class SkillWorkspaceMetadata(BaseModel):
    """Metadata for a skill workspace."""

    version: int = Field(default=0, description="Skill workspace version")
    """ skill workspace version"""

    created_at: Optional[datetime] = Field(default=None, description="The timestamp when the workspace was created")
    """ timestamp when the workspace was created"""

    updated_at: Optional[datetime] = Field(default=None, description="The timestamp when the workspace was updated")
    """ timestamp when the workspace was updated"""

    last_access: Optional[datetime] = Field(default=None,
                                            description="The timestamp when the workspace was last accessed")
    """ timestamp when the workspace was last accessed"""

    skills: dict[str, SkillMetadata] = Field(default_factory=dict, description="The skills in the workspace metadata")
    """ skills in the workspace metadata"""

    inputs: list[SkillWorkspaceInputRecord] = Field(default_factory=list,
                                                    description="The inputs in the workspace metadata")
    """ inputs in the workspace metadata"""

    outputs: list[SkillWorkspaceOutputRecord] = Field(default_factory=list,
                                                      description="The outputs in the workspace metadata")
    """ outputs in the workspace metadata"""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SkillWorkspaceMetadata':
        """Create WorkspaceMetadata instance from dictionary."""
        metadata = cls()
        metadata.version = data.get("version", 1)

        # Parse timestamps
        metadata.created_at = parse_datetime(data.get("created_at", None))
        metadata.updated_at = parse_datetime(data.get("updated_at", None))
        metadata.last_access = parse_datetime(data.get("last_access", None))

        # Parse skills
        skills_data = data.get("skills", {})
        metadata.skills = {name: SkillMetadata.from_dict(skill_data) for name, skill_data in skills_data.items()}

        # Parse inputs
        metadata.inputs = [SkillWorkspaceInputRecord.from_dict(input_data) for input_data in data.get("inputs", [])]

        # Parse outputs
        metadata.outputs = [
            SkillWorkspaceOutputRecord.from_dict(output_data) for output_data in data.get("outputs", [])
        ]

        return metadata

    def to_dict(self) -> Dict[str, Any]:
        """Convert SkillWorkspaceMetadata instance to dictionary for JSON serialization."""
        return {
            "version": self.version,
            "created_at": format_datetime(self.created_at),
            "updated_at": format_datetime(self.updated_at),
            "last_access": format_datetime(self.last_access),
            "skills": {
                name: skill.to_dict()
                for name, skill in self.skills.items()
            },
            "inputs": [input_rec.to_dict() for input_rec in self.inputs],
            "outputs": [output_rec.to_dict() for output_rec in self.outputs]
        }
