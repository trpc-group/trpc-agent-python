# Replay Consistency Harness

This harness runs the same deterministic replay cases against multiple storage
backends through the public `SessionServiceABC` and `MemoryServiceABC`
interfaces. Each case creates a session, appends fixed-id events with stable
timestamps and invocation ids, stores the resulting session through
`store_session(session)`, and queries memory with `search_memory(key, query,
limit)`. The default matrix runs real InMemory and SQLite services; Redis is
only added when `TRPC_AGENT_REPLAY_REDIS_URL` is set.

Snapshots normalize fields that should not affect replay semantics: raw
timestamps, summary timestamp values, auto-generated event ids, dict key order,
and memory timestamp values. Fixture event ids are preserved so duplicate,
retry, and wrong-id problems remain visible. Memory rows are sorted by
`(query, author, text, key)` because backend search order can differ.

The comparator strictly checks event order, roles, authors, text, tool call
args, tool responses, persisted state, memory content, summary text, summary
session id, summary overwrite behavior, summary event flags, and
historical_events. `DeterministicSessionSummarizer` avoids real LLM calls while
leaving the production summary compression path responsible for summary events
and historical event storage. SQLite uses temporary database files and explicit
SQL storage initialization; initialization failures are not silently ignored.

Reports include complete diffs plus `allowed_diffs` and `unallowed_diffs`
partitions, with counts per case and globally. Synthetic mutation tests and
real InMemory replay snapshot mutation tests intentionally drop, reorder,
duplicate, and alter event, state, memory, and summary fields to prove the
recursive diff can locate unallowed regressions by session, event, memory,
summary, path, left value, and right value.
