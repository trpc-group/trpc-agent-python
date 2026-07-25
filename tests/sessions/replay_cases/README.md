# Replay Consistency Design

The replay harness loads ten JSONL traces and applies each operation to a paired
SessionService and MemoryService. InMemory and SQLite run by default, so CI gets
a real persistence round trip without external infrastructure. Set
`TRPC_REPLAY_BACKENDS=inmemory` for the smallest mode. A disposable SQL or Redis
backend can be added with `TRPC_REPLAY_SQL_URL` or `TRPC_REPLAY_REDIS_URL`;
explicit backend selection uses `TRPC_REPLAY_BACKENDS=inmemory,sql,redis`.

Comparison removes only non-business variance. Physical event IDs map to logical
replay IDs, wall-clock timestamps become a typed marker, JSON objects are
compared structurally, and memory matches are sorted because equal-keyword
ranking is not part of the MemoryService contract. Summary text uses Unicode
NFKC, case folding, and whitespace collapse. Summary ownership, version,
supersedes relation, event counts, and active anchor count remain exact. The
report records any raw memory-order or recovery-mechanism difference under a
narrow `allowed_diffs` rule; content, count, state, event order, and summary
metadata are never globally ignored.

Duplicate append and failed summary replacement cases use compensating replay
recovery, then reload the backend to detect duplicate events, dirty state, stale
anchors, or an incorrect cached summary. Per-backend expectations keep
InMemory-only runs meaningful, while pairwise comparison exposes backend drift.
Every unexpected difference includes the logical session ID, JSON field path,
event index or summary ID when applicable, and both backend values. Run the
harness directly to refresh the report:

```bash
python -m tests.sessions.replay_harness \
  --output session_memory_summary_diff_report.json
```
