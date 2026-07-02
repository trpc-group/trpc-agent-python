# Replay Consistency Test Framework

tRPC-Agent supports InMemory, SQL, and Redis backends for Session/Memory storage. In production, developers often prototype with InMemory and then switch to SQL or Redis. If different backends produce inconsistent event order, state, memory, or summary data for the same agent trajectory, it leads to replay errors, context loss, long-term memory corruption, and summary overwrite issues.

This framework provides a set of standardized input trajectories to drive multiple backends, automatically generates diff reports, and pinpoints the field path and values of each inconsistency. It serves both as a testing tool and a quality benchmark for backend implementations.

## Architecture

Core components:

- **ReplayCase / ReplayStep**: JSONL files defining standardized input trajectories
- **ReplayHarness**: Parses JSONL steps, drives two backends in parallel, collects raw results
- **DiffEngine**: Four-dimension comparison (events / state / memory / summary), produces DiffReport
- **Normalizer**: Truncates timestamps to second precision, reassigns stable IDs by content, excludes `is_final_response`

Based on [tests/sessions/conftest.py](../../../tests/sessions/conftest.py) and [tests/sessions/test_replay_consistency.py](../../../tests/sessions/test_replay_consistency.py).

## Replay Cases

| # | Case Name | Type | Description |
|---|---|---|---|
| 1 | `single_turn` | Normal | Single user → agent exchange |
| 2 | `multi_turn` | Normal | 3 rounds of alternating conversation |
| 3 | `tool_call` | Normal | function_call + function_response |
| 4 | `state_update` | Normal | Multiple state_delta writes and overwrites |
| 5 | `memory_rw` | Normal | store_session + search_memory |
| 6 | `summary_gen` | Normal | 22-turn conversation triggering summary |
| 7 | `summary_truncate` | Known divergence | Two-layer validation: strict metadata + per-backend semantics |
| 8 | `exception_recovery` | Injected | inject_skip_append to simulate write failure |
| 9 | `injected_event_order` | Injected | inject_reorder_events to swap events |
| 10 | `injected_summary_session` | Injected | inject_summary_session_id to alter summary ownership |

## Normalization Strategy

Before cross-backend comparison, non-business differences are removed:

| Field | Treatment |
|-------|-----------|
| event.timestamp | Truncate to second precision (int) |
| event.id | Reassign stable ID sorted by content |
| state_delta | Unify JSON key ordering |
| is_final_response | Excluded (computed property differs across serialization paths) |

Three categories of differences are explicitly allowed and written to allowed_diff:

1. Backend-generated `invocation_id`
2. Backend-specific `save_key` format differences
3. Event count differences after summary compression (InMemory stores compressed events in memory; SQL get_session re-reads all raw events from the event table)

## Summary Comparison Strategy

The comparison operates in two layers:

1. **Summary metadata**: `session_id`, `summary_text`, `original_event_count`, `compressed_event_count` must be strictly consistent across backends — this is the core requirement for replay correctness
2. **Per-backend independent validation**: summary text is non-empty, compression has taken effect (compressed < original), and new events appended after compression are preserved

The exact boundary between summary text and retained events is allowed to differ due to backend storage model differences.

## Backend Access

| Mode | Backend A | Backend B | Trigger |
|------|-----------|-----------|---------|
| Lightweight (default) | InMemorySessionService | SqlSessionService(SQLite) | Always |
| SQL integration | InMemorySessionService | SqlSessionService(MySQL) | TEST_MYSQL_URL |
| Redis integration | InMemorySessionService | RedisSessionService | TEST_REDIS_URL |

All three backends conform to the `SessionServiceABC` interface; adding a new backend only requires implementing that interface.
