# Replay Consistency Harness

This harness verifies that session, memory, and summary behavior replays
consistently across storage backends. Python `ReplayCase` fixtures define the
executable operation DSL: create a session, append deterministic events, create
summaries at fixed points, store the final session to memory, run memory
queries, and normalize the resulting snapshot. The JSONL manifest in
`tests/sessions/replay_cases/session_memory_summary_replay_cases.jsonl`
mirrors the registry for review readability and is checked by tests.

The default CI matrix is intentionally light: InMemory plus temporary SQLite
files under `tmp_path`. Optional integration backends are only enabled through
environment variables: `TRPC_AGENT_REPLAY_SQL_URL` for an external SQL backend
and `TRPC_AGENT_REPLAY_REDIS_URL` for Redis. When these variables are absent,
the report records the backend as skipped instead of making CI depend on
external services.

Normalization removes or canonicalizes non-semantic variance: exact timestamps,
summary timestamp values, auto-generated event ids, dict serialization order,
and memory timestamp values. Fixture event ids are preserved so duplicate,
retry, and wrong-id regressions remain visible. Memory search order is sorted
by stable content keys because ranking differs by backend.

Allowed diffs are deliberately narrow: backend name, raw timestamp values, and
timestamp presence. Event order, author/role/text, tool arguments and results,
state values, memory scope/content, summary text, latest-summary overwrite
semantics, summary session id, summary event flags, and historical events are
strict. Summary text is whitespace-normalized but not semantically relaxed.

Diff reports include backend statuses, normal replay false-positive counts,
mutation detection summaries, and structured diff entries with case, backend,
session, event, memory, summary, path, left, and right fields. Tests write
runtime reports to `tmp_path`; the repository root JSON is only a deterministic
schema example. The checked-in mutation example lives at
`tests/sessions/replay_consistency/session_memory_summary_mutation_report.json`.
