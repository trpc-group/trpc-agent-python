# Issue 89 Replay Consistency Design

The replay harness loads public JSONL traces and applies the same operations to InMemory and SQLite-backed Session and Memory services. Each run writes user and assistant events, tool call and tool response parts, state deltas, memory store/search operations, and summary creation through the SDK public service APIs. The resulting backend state is converted into a normalized snapshot before comparison.

Normalization removes non-business noise: SDK-generated event ids, raw timestamps, and JSON field ordering are not compared directly. Event order, authors, invocation ids, content parts, state deltas, memory search results, summary anchors, and summary coverage are still compared. Backend-specific tolerances are recorded as `allowed_diffs` rather than silently ignored.

Summary comparison is split into text, metadata, and coverage checks. Summary text is compared after deterministic generation. Session ownership, anchor presence, observable revision state, replacement behavior, timestamp validity, and summary-plus-recent-event context are checked separately. The current SDK does not expose a dedicated persisted `summary.version` field, so the harness interprets version as observable revision state: v1/v2 generation order, current persisted summary text, anchor ownership, and coverage/projection consistency.

SQLite is the default persistent backend for light mode, so no external database is required. Redis integration is environment-gated by `TRPC_AGENT_REPLAY_REDIS_URL` and skipped otherwise. The report `session_memory_summary_diff_report.json` records each case, backend pair, field path, session id, event index or summary id, and both compared values.
