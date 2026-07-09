# Session / Memory / Summary Replay Consistency Harness — Design

## Objective

Validate that InMemory, SQL, and Redis backends for Session, Memory, and Summary
services produce equivalent results when driven by identical operation sequences.
This harness replays standardized input trajectories through multiple backends
and generates a structured diff report.

## Architecture

```
replay_cases/*.json   →  ReplayEngine  →  BackendResult
                             │
                    ┌─────────┴─────────┐
              Backend A            Backend B
              (InMemory)           (SQL / Redis)
                    │                    │
              BackendResult        BackendResult
                    │                    │
                    └────────┬───────────┘
                             ▼
                      _normalizer.py
                      (strip timestamps, IDs, sort keys)
                             │
                    NormalizedResult A + B
                             │
                             ▼
                      _comparator.py
                      (pairwise diff events, state, memory, summary)
                             │
                             ▼
                      DiffReport (JSON)
```

Each replay case is a JSON file describing a sequence of operations:
`create_session`, `append_event`, `update_state`, `inject_summary`,
`store_memory`, `search_memory`, `read_back`.

The ReplayEngine executes the same sequence against two backends in parallel,
collecting raw results. These are then normalized and compared.

## Normalization Strategy

Fields that differ across backends by design are normalized before comparison:

| Field                  | Strategy                                                 |
|------------------------|----------------------------------------------------------|
| `event.id`             | Stripped (auto-generated UUID per backend)               |
| `event.timestamp`      | Replaced with sequential index (0, 1, 2, ...)            |
| `session.last_update_time` | Replaced with sentinel `0.0`                          |
| `summary_timestamp`    | Replaced with sentinel `0.0`                             |
| `memory_entry.timestamp` | Stripped                                               |
| dict key ordering      | Re-serialized with `sort_keys=True`                      |
| `invocation_id`        | Stripped (invocation-scoped, not backend-scoped)         |
| `branch`, `request_id` | Stripped (runtime metadata, not persisted uniformly)     |

## Summary Comparison Strategy

The harness distinguishes two levels of summary correctness:

1. **Content semantic consistency** — the `summary_text` is compared after
   whitespace normalization. Minor formatting differences that do not change
   meaning are treated as allowed diffs on a per-backend-pair basis.

2. **Metadata integrity** — `session_id`, `original_event_count`, and
   `compressed_event_count` must match exactly across backends. Any deviation
   in these fields is an unconditional failure. Three classes of summary bug
   are explicitly detected:
   - **Summary loss** — summary present in backend A, absent in backend B.
   - **Summary overwrite** — summary exists but with wrong `session_id`.
   - **Wrong session affiliation** — summary is stored under the wrong session
     key in the underlying cache or storage.

Summary injection into the `SummarizerSessionManager._summarizer_cache`
bypasses the LLM, since the harness tests storage consistency, not
summarization model quality.

## Allowed Differences

Not all field-level differences indicate a bug. Known, documented divergences
are captured in `AllowedDiff` rules:

```
allowed_diffs = {
    "inmem_vs_sql": [
        {"field": "events[*].function_calls[*].args", "reason": "SQL serializes
         JSON args differently from InMemory dict round-trip"},
    ],
    "inmem_vs_redis": [
        {"field": "events[*].timestamp_precision",
         "reason": "Redis stores timestamps as float strings with limited precision"},
    ],
}
```

Each rule includes the backend pair, the field path, and a justification.
Diffs matching an allowed rule are suppressed from the failure count but
still appear in the report tagged `allowed: true`.

## Backend Integration

| Backend  | Availability     | Activation                                 |
|----------|-----------------|---------------------------------------------|
| InMemory | Always           | Direct instantiation                        |
| SQL      | Always (sqlite)  | `sqlite:///:memory:` — no external deps     |
| Redis    | Opt-in           | `TRPC_REDIS_URL` env var; skipped otherwise |

The lightweight mode (InMemory + SQL) runs in CI without external services.
Redis integration mode requires the environment variable to be set; when
absent, Redis test pairs are skipped with a descriptive message.

## Diff Report

The report (`session_memory_summary_diff_report.json`) contains:

- **run metadata**: run ID, timestamp, backends tested.
- **per-case results**: status (pass/fail/error), list of `DiffEntry` objects.
- **summary**: total/pass/fail counts and false-positive rate.

Each `DiffEntry` pinpoints: backend pair, session ID, event index (or summary
ID), category (events/state/memory/summary), full dotted field path, and the
two conflicting values.

## Testing the Harness Itself

- `test_replay_normalizer.py` — unit tests for every normalization function.
- `test_replay_comparator.py` — unit tests for diff logic, including targeted
  tests for summary loss, overwrite, and mis-affiliation detection.
- `test_replay_report.py` — unit tests for report generation and serialization.
- `test_replay_consistency.py` — E2E tests running all 10 replay cases through
  backend pairs, asserting normal cases produce ≤5% false positives and anomaly
  cases are 100% detected.
