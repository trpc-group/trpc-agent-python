# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Replay consistency harness for cross-backend Session/Memory/Summary verification.

This package provides a framework to replay standardized operation sequences
across multiple backends (InMemory, SQL, Redis) and compare their outputs
to detect inconsistencies in events, state, memory, and session summaries.

== Design Notes ==

Normalization Strategy:
    Backend implementations differ in timestamp precision (float vs DB
    integer), auto-generated IDs (event_id, invocation_id), JSON key
    ordering, and null/empty representations. The Normalizer applies five
    strategies: (1) timestamps are rounded to 3 decimal places; (2) auto-
    generated IDs are replaced with the placeholder "<auto>"; (3) dict keys
    are sorted alphabetically; (4) None, "", [], {} are unified to None;
    (5) summary text is whitespace-normalized and Unicode punctuation is
    converted to ASCII.

Summary Comparison Strategy:
    Three layers. Layer 1 (storage metadata): session_id, original_event_count,
    and compressed_event_count are compared as strict equality. Layer 2
    (content semantics): summary_text is normalized before comparison. Layer 3
    (non-business metadata): summary_timestamp is treated as an allowed_diff.
    Three critical issues are guaranteed 100% detection: summary_loss (one
    backend has summary, another does not), summary_ownership_error (session_id
    mismatch), and summary_overwrite_error (original_event_count mismatch).

Allowed Differences:
    19 field-level rules using fnmatch patterns (e.g., "*.timestamp", "*.id").
    Each rule targets a specific field path with a documented justification.
    No blanket ignore is applied — every diff must match an explicit rule.

Backend Integration:
    Three-tier control: (1) CLI flags (--run-sql, --run-redis, --run-integration);
    (2) environment variables (TRPC_TEST_REDIS_URL, TRPC_TEST_RUN_SQL);
    (3) CI auto-detection ($CI). SQL uses SQLite by default, requiring no
    external database. Unavailable backends call pytest.skip(), never fail.
"""

from .replay_loader import ReplayLoader
from .backend_executor import BackendExecutor
from .snapshot import BackendSnapshot
from .normalizer import Normalizer
from .comparator import Comparator
from .diff_report import DiffReport

__all__ = [
    "ReplayLoader",
    "BackendExecutor",
    "BackendSnapshot",
    "Normalizer",
    "Comparator",
    "DiffReport",
]