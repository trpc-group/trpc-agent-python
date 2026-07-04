# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Replay Consistency Harness — Design Notes

# Normalization Strategy

Backend implementations differ in timestamp precision (float vs DB integer),
auto-generated IDs (event_id, invocation_id), JSON key ordering, and null/empty
representations. The Normalizer applies five strategies to eliminate these
non-business differences before comparison: (1) timestamps are rounded to 3
decimal places; (2) auto-generated IDs are replaced with the placeholder
"<auto>"; (3) dict keys are sorted alphabetically; (4) None, "", [], {} are
unified to None; (5) summary text is whitespace-normalized and Unicode
punctuation is converted to ASCII.

# Summary Comparison Strategy

Summary comparison operates on three layers. Layer 1 (storage metadata):
session_id, original_event_count, and compressed_event_count are compared as
strict equality — any mismatch is flagged as an unallowed diff. Layer 2 (content
semantics): summary_text is normalized (whitespace, punctuation) before
comparison. Layer 3 (non-business metadata): summary_timestamp is treated as
an allowed_diff due to backend latency differences.

Three critical issue categories are guaranteed 100% detection: summary_loss
(when one backend has a summary and another does not), summary_ownership_error
(when summary.session_id mismatches), and summary_overwrite_error (when
original_event_count differs, indicating incomplete overwrite).

# Allowed Differences

19 field-level allowed_diff rules are defined using fnmatch patterns. Each rule
targets a specific field path (e.g., "*.timestamp", "*.id") with a documented
justification. No blanket ignore is applied — every diff must match an explicit
rule or be reported as an unallowed diff.

# Backend Integration

Backends are integrated via pytest fixtures with three-tier control:
(1) command-line flags (--run-sql, --run-redis, --run-integration);
(2) environment variables (TRPC_TEST_REDIS_URL, TRPC_TEST_RUN_SQL);
(3) CI auto-detection ($CI). SQL uses SQLite by default, requiring no
external database. Redis requires TRPC_TEST_REDIS_URL. When a backend is
unavailable, its fixture calls pytest.skip(), never fails.
"""