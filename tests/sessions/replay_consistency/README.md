# Session / Memory / Summary Replay Consistency

This package implements the lightweight replay harness for Issue #89. It is intentionally test-side only: no production API, storage schema, or runtime dependency is changed.

## Default Contract

- Default lightweight comparison is `InMemoryReplayAdapter` vs `SQLiteReplayAdapter`.
- The lightweight suite uses temporary SQLite files and a deterministic fake summarizer, so it does not require Redis, MySQL, PostgreSQL, network access, or a real LLM.
- Integration tests are opt-in. They run only when their explicit environment switches and backend URLs are set.
- Replay operations are Python fixtures because the SDK event model uses typed `Content`, `Part`, `FunctionCall`, and `FunctionResponse` objects. The checked-in `replay_cases_manifest.json` summarizes the public coverage in a review-friendly format.
- `acceptance_matrix.json` separates the 10 required public cases from the extended all-entities contract case.

## Design Note

The harness replays deterministic operation sequences through the SDK service APIs, then reads observable state back through those same services before comparing snapshots. Canonicalization is intentionally minimal: generated event IDs are mapped to logical client IDs, dictionary keys are sorted, Unicode text is normalized, and backend metadata is excluded; event order, state values, tool call/response linkage, memory scope, summary ownership, and summary coverage remain strict. Summary comparison is split between stored facts and derived semantics because the SDK does not expose persisted summary revision or lineage fields. Allowed differences must be explicit, localized, and justified, so backend drift cannot be hidden by broad ignores. Default execution uses InMemory and temporary SQLite plus a deterministic fake summarizer, keeping CI lightweight while integration adapters allow real SQL and Redis checks when environment variables are provided.

## SDK API Path

```text
ReplayCase
  -> ReplayBackendAdapter
  -> trpc_agent_sdk public SessionService / MemoryService APIs
  -> Backend storage
  -> fresh service read via SnapshotReader
  -> canonicalize_snapshot
  -> semantic oracle + field diff + report
```

Adapters do not implement their own storage model. They call `create_session`, `append_event`, `create_session_summary`, `get_session`, `store_session`, and `search_memory` on real SDK services.

## Summary Oracle

The SDK currently exposes summary behavior through summary events, historical events, and `SummarizerSessionManager`; it does not persist a public `version`, `supersedes`, or coverage model. The harness therefore records derived summary metadata on the test side:

- `version` is the per-session summary creation order observed during replay.
- `covered_event_ids` are the logical client event IDs selected by `find_events_for_summary` before summary creation.
- `active` is derived from the active summary event after replay.
- `session_id`, `user_id`, and `app_name` are strict ownership checks and must match the session being replayed.

These derived fields are not production API claims. They are a semantic oracle for detecting summary loss, stale overwrite, wrong-session ownership, and coverage drift without changing SDK schema.

## Reports

Each run writes structured JSON reports with:

- `case_id`
- `backend_pair`
- aggregate metrics
- `session_id`
- `entity_type`
- `entity_id`
- `index`
- `field_path`
- `reference_value`
- `actual_value`
- `category`
- `allowed`
- `reason`

Set `REPLAY_REPORT_DIR` to retain reports outside pytest temporary directories. CI uses this to upload replay artifacts.

## Commands

```bash
python -m pytest tests/sessions/replay_consistency/test_replay_consistency.py -q -m replay_lightweight
python -m pytest tests/sessions/replay_consistency -q
RUN_REPLAY_SQL_INTEGRATION=1 DATABASE_URL=sqlite:////tmp/replay.db python -m pytest tests/sessions/replay_consistency/test_replay_integration.py -q -m replay_integration
RUN_REPLAY_REDIS_INTEGRATION=1 REDIS_URL=redis://localhost:6379/15 python -m pytest tests/sessions/replay_consistency/test_replay_integration.py -q -m replay_integration
```
