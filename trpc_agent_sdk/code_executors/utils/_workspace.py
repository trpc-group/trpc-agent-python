# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Workspace utilities for TRPC Agent framework.

This module provides utility functions for workspace operations.
"""

from __future__ import annotations

from typing import List
from typing import Optional

from .._constants import DEFAULT_EXEC_FILE_MODE
from .._constants import DEFAULT_SCRIPT_FILE_MODE
from .._constants import DIR_OUT
from .._constants import DIR_SKILLS
from .._constants import DIR_WORK
from .._constants import NORMALIZE_GLOBS_BACKSLASH
from .._constants import NORMALIZE_GLOBS_OUT
from .._constants import NORMALIZE_GLOBS_SKILLS
from .._constants import NORMALIZE_GLOBS_SLASH
from .._constants import NORMALIZE_GLOBS_VAR_LBRACE
from .._constants import NORMALIZE_GLOBS_VAR_PREFIX
from .._constants import NORMALIZE_GLOBS_VAR_RBRACE
from .._constants import NORMALIZE_GLOBS_WORK
from .._constants import NORMALIZE_GLOBS_WORKSPACE
from .._constants import NORMALIZE_GLOBS_WORKSPACE_DIR
from .._types import CodeBlock


def _trim_glob_separator(s: str, ) -> str:
    """
    Trim leading path separator from string.
    """
    if not s:
        return s

    if s.startswith(NORMALIZE_GLOBS_SLASH) or s.startswith(NORMALIZE_GLOBS_BACKSLASH):
        return s[1:]

    return s


def _normalize_glob_tail(
    tail: str,
    dir_name: str,
) -> str:
    """
    Normalize the tail part of a glob pattern.
    """
    if not tail:
        if dir_name == NORMALIZE_GLOBS_WORKSPACE_DIR:
            return NORMALIZE_GLOBS_WORKSPACE_DIR
        return dir_name

    r = _trim_glob_separator(tail)

    if dir_name == NORMALIZE_GLOBS_WORKSPACE_DIR:
        if not r:
            return NORMALIZE_GLOBS_WORKSPACE_DIR
        return r

    if not r:
        return dir_name

    return dir_name + NORMALIZE_GLOBS_SLASH + r


def _normalize_glob_prefix(
    s: str,
    name: str,
    dir_name: str,
) -> str:
    """
    Normalize a single glob prefix.
    """
    brace_prefix = NORMALIZE_GLOBS_VAR_LBRACE + name + NORMALIZE_GLOBS_VAR_RBRACE
    if s.startswith(brace_prefix):
        return _normalize_glob_tail(s[len(brace_prefix):], dir_name)

    simple_prefix = NORMALIZE_GLOBS_VAR_PREFIX + name
    if s.startswith(simple_prefix):
        return _normalize_glob_tail(s[len(simple_prefix):], dir_name)

    return s


def build_block_spec(
    idx: int,
    block: CodeBlock,
) -> tuple[str, int, str, Optional[List[str]]]:
    """
    Map a code block into file name, mode, command, and arguments.

    Supports Python and Bash languages.

    Args:
        idx: Block index for file naming
        block: Code block to process

    Returns:
        Tuple of (filename, mode, command, args)

    Raises:
        ValueError: If language is unsupported
    """
    lang = (block.language or "").strip().lower()

    if lang in ("python", "py", "python3"):
        return (
            f"code_{idx}.py",
            DEFAULT_SCRIPT_FILE_MODE,
            "python3",
            None,
        )
    elif lang in ("bash", "sh"):
        return (
            f"code_{idx}.sh",
            DEFAULT_EXEC_FILE_MODE,
            "bash",
            None,
        )
    else:
        raise ValueError(f"unsupported language: {block.language}")


def normalize_globs(patterns: List[str], ) -> List[str]:
    """
    Rewrite glob patterns with environment-style prefixes.

    Converts patterns like $OUTPUT_DIR/a.txt to out/a.txt.
    Understands: WORKSPACE_DIR, SKILLS_DIR, WORK_DIR, OUTPUT_DIR.

    Args:
        patterns: List of glob patterns

    Returns:
        List of normalized patterns

    Examples:
        $OUTPUT_DIR/a.txt   -> out/a.txt
        ${WORK_DIR}/x/**    -> work/x/**
        $WORKSPACE_DIR/out  -> out
    """
    if not patterns:
        return []

    out = []
    for p in patterns:
        s = p.strip()
        if not s:
            continue

        s = _normalize_glob_prefix(
            s,
            NORMALIZE_GLOBS_WORKSPACE,
            NORMALIZE_GLOBS_WORKSPACE_DIR,
        )
        s = _normalize_glob_prefix(s, NORMALIZE_GLOBS_SKILLS, DIR_SKILLS)
        s = _normalize_glob_prefix(s, NORMALIZE_GLOBS_WORK, DIR_WORK)
        s = _normalize_glob_prefix(s, NORMALIZE_GLOBS_OUT, DIR_OUT)
        out.append(s)

    return out
