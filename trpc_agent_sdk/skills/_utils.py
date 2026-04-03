# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Utils for TRPC Agent skills system.

This module provides utility functions for the skills system.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import Optional
from typing import Union

from trpc_agent_sdk.code_executors import DIR_OUT
from trpc_agent_sdk.code_executors import DIR_RUNS
from trpc_agent_sdk.code_executors import DIR_SKILLS
from trpc_agent_sdk.code_executors import DIR_WORK
from trpc_agent_sdk.code_executors import META_FILE_NAME
from trpc_agent_sdk.code_executors import TMP_FILE_NAME
from trpc_agent_sdk.context import InvocationContext

from ._types import SkillWorkspaceMetadata


def compute_dir_digest(root_path: Union[Path, str]) -> str:
    """Compute a stable digest of a directory tree."""
    files = []
    if isinstance(root_path, str):
        root_path = Path(root_path)
    # Walk directory tree and collect file relative paths
    for file_path in root_path.rglob('*'):
        if file_path.is_file():
            rel_path = file_path.relative_to(root_path)
            files.append(str(rel_path))

    # Sort files for stability
    files.sort()

    # Compute SHA256 digest
    h = hashlib.sha256()
    for rel in files:
        # Normalize to forward slash for cross-platform consistency
        normalized_path = rel.replace(os.path.sep, '/')
        h.update(normalized_path.encode('utf-8'))
        h.update(b'\x00')

        # Read file content and update hash
        file_full_path = root_path / rel
        with open(file_full_path, 'rb') as f:
            content = f.read()
            h.update(content)
            h.update(b'\x00')

    return h.hexdigest()


def save_metadata(root: Union[Path, str], metadata: SkillWorkspaceMetadata) -> None:
    """Save workspace metadata to metadata.json file.

    Args:
        root: Root directory path
        metadata: WorkspaceMetadata instance to save
    """
    if isinstance(root, str):
        root = Path(root)
    metadata.updated_at = datetime.now()
    metadata_file = root / META_FILE_NAME
    temp_file = root / TMP_FILE_NAME

    with open(temp_file, 'w', encoding='utf-8') as f:
        json.dump(metadata.to_dict(), f, indent=2, default=str)

    os.replace(temp_file, metadata_file)


def ensure_layout(root: Union[Path, str]) -> dict[str, Path]:
    """Create standard workspace subdirectories and metadata file when absent.

    Args:
        root: Root directory path of the workspace

    Returns:
        Dictionary mapping directory names to Path objects

    Raises:
        OSError: If directory creation fails
    """
    if isinstance(root, str):
        root = Path(root)
    paths = {
        DIR_SKILLS: root / DIR_SKILLS,
        DIR_WORK: root / DIR_WORK,
        DIR_RUNS: root / DIR_RUNS,
        DIR_OUT: root / DIR_OUT,
    }

    # Create all subdirectories
    for path in paths.values():
        if not path.exists():
            os.makedirs(path, mode=0o755, exist_ok=True)

    # Initialize metadata if missing
    metadata_file = root / META_FILE_NAME
    if not metadata_file.exists():
        metadata = SkillWorkspaceMetadata()
        save_metadata(root, metadata)

    return paths


def load_metadata(root: Union[Path, str]) -> SkillWorkspaceMetadata:
    """Load metadata.json from workspace root.

    When missing, an empty metadata with defaults is returned without error.

    Args:
        root: Root directory path of the workspace

    Returns:
        SkillWorkspaceMetadata instance

    Raises:
        OSError: If file exists but cannot be read (other than file not found)
        json.JSONDecodeError: If file contains invalid JSON
    """
    if isinstance(root, str):
        root = Path(root)
    metadata_file = root / META_FILE_NAME

    if not metadata_file.exists():
        # Return empty metadata with defaults when file doesn't exist
        return SkillWorkspaceMetadata()

    try:
        with open(metadata_file, 'r', encoding='utf-8') as f:
            data = json.load(f)

        return SkillWorkspaceMetadata.from_dict(data)

    except json.JSONDecodeError as ex:
        raise ValueError(f"Invalid JSON in metadata file: {ex}")
    except OSError as ex:
        raise OSError(f"Failed to read metadata file: {ex}")


def shell_quote(s: str) -> str:
    """
    Wrap a string for safe single-quoted usage in a POSIX shell.

    It escapes embedded single quotes by closing, inserting an escaped quote,
    and reopening.

    Args:
        s: The string to quote

    Returns:
        The safely quoted string
    """
    if not s:
        return "''"
    q = s.replace("'", "'\\''")
    return f"'{q}'"


def set_state_delta(invocation_context: InvocationContext, key: str, value: Any) -> None:
    """Set the state delta of a skill workspace.

    Args:
        invocation_context: InvocationContext object
        key: Key to set
        value: Value to set
    """
    invocation_context.actions.state_delta[key] = value


def get_state_delta(invocation_context: InvocationContext, key: str) -> Optional[Any]:
    """Get the state delta of a skill workspace.

    Args:
        invocation_context: InvocationContext object
        key: Key to get

    Returns:
        Value of the key or None if not found
    """
    state = dict(invocation_context.session_state.copy())
    state.update(invocation_context.actions.state_delta)
    return state.get(key, None)
