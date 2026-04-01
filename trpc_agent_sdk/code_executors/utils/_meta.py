# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""
Workspace metadata helpers and constants.

This module holds workspace metadata helpers and constants for managing
workspace structure, skill staging, and input/output tracking.
"""

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import BaseModel
from pydantic import Field

from .._constants import DIR_OUT
from .._constants import DIR_RUNS
from .._constants import DIR_SKILLS
from .._constants import DIR_WORK
from .._constants import META_FILE_NAME


class SkillMeta(BaseModel):
    """
    Records a staged skill snapshot.
    """

    name: Optional[str] = None
    rel_path: Optional[str] = None
    digest: Optional[str] = None
    mounted: Optional[bool] = None
    staged_at: Optional[datetime] = Field(default=None, alias="staged_at")


class InputRecordMeta(BaseModel):
    """
    Tracks a staged input resolution.
    """

    src: Optional[str] = Field(default=None, alias="from")
    dst: Optional[str] = None
    resolved: Optional[str] = None
    version: Optional[int] = None
    mode: Optional[str] = None
    timestamp: Optional[datetime] = Field(default=None, alias="ts")


class OutputRecordMeta(BaseModel):
    """
    Tracks an output collection run.
    """

    globs: list[str] = Field(default_factory=list)
    saved_as: list[str] = Field(default_factory=list)
    versions: list[int] = Field(default_factory=list)
    limits_hit: Optional[bool] = None
    timestamp: Optional[datetime] = Field(default=None, alias="ts")


class WorkspaceMetadata(BaseModel):
    """
    Describes staged skills and recent activity.
    """

    version: Optional[int] = None
    created_at: Optional[datetime] = Field(default=None, alias="created_at")
    updated_at: Optional[datetime] = Field(default=None, alias="updated_at")
    last_access: Optional[datetime] = Field(default=None, alias="last_access")
    skills: dict[str, SkillMeta] = Field(default_factory=dict)
    inputs: list[InputRecordMeta] = Field(default_factory=list)
    outputs: list[OutputRecordMeta] = Field(default_factory=list)


def ensure_layout(root: Path | str) -> dict[str, Path]:
    """
    Create standard workspace subdirectories and a metadata file when absent.

    Returns full paths for convenience.

    Args:
        root: Workspace root directory path

    Returns:
        Dictionary mapping directory names to full paths

    Raises:
        OSError: If directory creation or file operations fail
    """
    if isinstance(root, str):
        root = Path(root)
    paths = {
        DIR_SKILLS: root / DIR_SKILLS,
        DIR_WORK: root / DIR_WORK,
        DIR_RUNS: root / DIR_RUNS,
        DIR_OUT: root / DIR_OUT,
    }

    for p in paths.values():
        Path(p).mkdir(parents=True, exist_ok=True)

    # Initialize metadata if missing
    meta_file = root / META_FILE_NAME
    if not meta_file.exists():
        now = datetime.now()
        md = WorkspaceMetadata(
            version=1,
            created_at=now,
            updated_at=now,
            last_access=now,
            skills={},
        )
        save_metadata(root, md)

    return paths


def load_metadata(root: Path | str) -> WorkspaceMetadata:
    """
    Load metadata.json from workspace root.

    When missing, an empty metadata with defaults is returned without error.

    Args:
        root: Workspace root directory path

    Returns:
        WorkspaceMetadata object

    Raises:
        OSError: If file read fails (except for not found)
        json.JSONDecodeError: If JSON parsing fails
    """
    if isinstance(root, str):
        root = Path(root)
    meta_file = root / META_FILE_NAME

    if not meta_file.exists():
        now = datetime.now()
        return WorkspaceMetadata(
            version=1,
            created_at=now,
            updated_at=now,
            last_access=now,
            skills={},
        )

    content = meta_file.read_text(encoding="utf-8")
    data = json.loads(content)

    # Convert datetime strings to datetime objects
    if "created_at" in data and data["created_at"]:
        data["created_at"] = datetime.fromisoformat(data["created_at"].replace("Z", "+00:00"))
    if "updated_at" in data and data["updated_at"]:
        data["updated_at"] = datetime.fromisoformat(data["updated_at"].replace("Z", "+00:00"))
    if "last_access" in data and data["last_access"]:
        data["last_access"] = datetime.fromisoformat(data["last_access"].replace("Z", "+00:00"))

    # Convert skills
    if "skills" in data and data["skills"]:
        for skill_data in data["skills"].values():
            if skill_data.get("staged_at", None):
                skill_data["staged_at"] = datetime.fromisoformat(skill_data["staged_at"].replace("Z", "+00:00"))

    # Convert inputs
    if "inputs" in data and data["inputs"]:
        for input_rec in data["inputs"]:
            if "ts" in input_rec and input_rec["ts"]:
                input_rec["ts"] = datetime.fromisoformat(input_rec["ts"].replace("Z", "+00:00"))

    # Convert outputs
    if "outputs" in data and data["outputs"]:
        for output_rec in data["outputs"]:
            if "ts" in output_rec and output_rec["ts"]:
                output_rec["ts"] = datetime.fromisoformat(output_rec["ts"].replace("Z", "+00:00"))

    return WorkspaceMetadata(**data)


def save_metadata(
    root: Path | str,
    md: WorkspaceMetadata,
) -> None:
    """
    Write metadata.json to the workspace root.

    Args:
        root: Workspace root directory path
        md: WorkspaceMetadata object to save

    Raises:
        OSError: If file write or rename fails
        json.JSONEncodeError: If JSON encoding fails
    """
    if isinstance(root, str):
        root = Path(root)
    md.updated_at = datetime.now()

    # Convert to dict and handle datetime serialization
    data = md.model_dump(exclude_none=True, by_alias=True)

    # Recursively convert datetime objects
    def convert_datetimes(d):
        if isinstance(d, dict):
            return {k: convert_datetimes(v) for k, v in d.items()}
        elif isinstance(d, list):
            return [convert_datetimes(item) for item in d]
        elif isinstance(d, datetime):
            return d.isoformat()
        return d

    data = convert_datetimes(data)

    buf = json.dumps(data, indent=2, ensure_ascii=False)

    tmp_file = root / ".metadata.tmp"
    meta_file = root / META_FILE_NAME

    tmp_file.write_text(buf, encoding="utf-8")
    tmp_file.chmod(0o600)
    tmp_file.rename(meta_file)


def dir_digest(root: Path) -> str:
    """
    Compute a stable digest of a directory tree.

    Walks the tree, sorts entries, and hashes relative path and contents.

    Args:
        root: Root directory path to compute digest for

    Returns:
        Hexadecimal digest string

    Raises:
        OSError: If directory walk or file read fails
    """
    files = []

    for file_path in root.rglob("*"):
        if file_path.is_file():
            rel_path = file_path.relative_to(root)
            files.append(rel_path)

    # Sort for stability
    files.sort()

    h = hashlib.sha256()

    for rel_path in files:
        # Normalize to slash for stability
        normalized = str(rel_path).replace(os.sep, "/")
        h.update(normalized.encode("utf-8"))
        h.update(b"\x00")

        file_path = root / rel_path
        content = file_path.read_bytes()
        h.update(content)
        h.update(b"\x00")

    return h.hexdigest()
