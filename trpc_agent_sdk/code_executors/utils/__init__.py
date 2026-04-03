# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Code execution utilities for TRPC Agent framework.

This module provides utility functions for processing code blocks,
extracting code from responses, and handling code execution results.
"""

from ._code_execution import CodeExecutionUtils
from ._files import collect_files_with_glob
from ._files import copy_dir
from ._files import copy_path
from ._files import detect_content_type
from ._files import get_rel_path
from ._files import make_symlink
from ._files import make_tree_read_only
from ._files import path_join
from ._meta import InputRecordMeta
from ._meta import OutputRecordMeta
from ._meta import SkillMeta
from ._meta import WorkspaceMetadata
from ._meta import dir_digest
from ._meta import ensure_layout
from ._meta import load_metadata
from ._meta import save_metadata
from ._workspace import build_block_spec
from ._workspace import normalize_globs

__all__ = [
    "CodeExecutionUtils",
    "collect_files_with_glob",
    "copy_dir",
    "copy_path",
    "detect_content_type",
    "get_rel_path",
    "make_symlink",
    "make_tree_read_only",
    "path_join",
    "InputRecordMeta",
    "OutputRecordMeta",
    "SkillMeta",
    "WorkspaceMetadata",
    "dir_digest",
    "ensure_layout",
    "load_metadata",
    "save_metadata",
    "build_block_spec",
    "normalize_globs",
]
