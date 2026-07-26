# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Replay consistency framework for SessionService and MemoryService backends.

Provides a deterministic harness that drives multiple Session/Memory backends
(InMemory, SQLite, MySQL, Redis) through the same JSONL-defined trajectories,
then emits a structured DiffReport that pinpoints every inconsistent field.

Public surface:
    - :class:`ReplayHarness`  – the main runner.
    - :class:`DiffEngine`     – four-dimension snapshot comparator.
    - :func:`load_replay_cases` – parse JSONL cases.
    - :func:`run_replay_harness` / :func:`build_diff_report` – CLI-friendly entry.
    - Module constants: :data:`NORMALIZATION_RULES`, :data:`ALLOWED_DIFF_RULES`.
"""
from __future__ import annotations

from ._backends import ReplayBackend
from ._backends import resolve_backend_names
from ._cases import DEFAULT_CASES_PATH
from ._cases import load_replay_cases
from ._diff import ALLOWED_DIFF_RULES
from ._diff import NORMALIZATION_RULES
from ._diff import build_diff_report
from ._diff import validate_expectations
from ._harness import run_replay_harness
from ._harness import write_diff_report
from ._main import main
from ._normalizer import normalize_summary_text

__all__ = [
    "DEFAULT_CASES_PATH",
    "ReplayBackend",
    "NORMALIZATION_RULES",
    "ALLOWED_DIFF_RULES",
    "normalize_summary_text",
    "load_replay_cases",
    "resolve_backend_names",
    "validate_expectations",
    "build_diff_report",
    "write_diff_report",
    "run_replay_harness",
    "main",
]