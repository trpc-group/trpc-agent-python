# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Replay consistency framework for SessionService and MemoryService backends.

Provides a deterministic harness that drives multiple Session/Memory backends
(InMemory, SQLite, MySQL, Redis) through the same JSONL-defined trajectories,
then emits a structured DiffReport that pinpoints every inconsistent field.

Public surface (see ``__all__`` for the canonical list):
    - :func:`run_replay_harness` – the main runner; orchestrates case playback
      across the requested backends and collects per-backend snapshots.
    - :func:`build_diff_report`   – four-dimension snapshot comparator;
      produces the structured DiffReport dict.
    - :func:`load_replay_cases`   – parse JSONL cases.
    - :func:`write_diff_report`   – serialize the report to disk.
    - :func:`validate_expectations` – per-backend invariant checker used
      by ``InMemory``-only mode.
    - :func:`resolve_backend_names` / :class:`ReplayBackend` – backend
      registry helpers.
    - Module constants: :data:`NORMALIZATION_RULES`, :data:`ALLOWED_DIFF_RULES`.
    - :func:`main`                – CLI entry point.
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