# Issue #89 Design Document: Session/Memory Replay Consistency Test Framework

> **Author**: coder-mtj
> **Reference**: `trpc-agent-go/session/replaytest/` (Go implementation)

## Overview

The Replay Consistency Test Framework verifies that session, memory, summary,
and track-event operations produce **identical results** across different storage
backends (InMemory, SQLite, Redis). By replaying the same sequence of operations
and comparing normalized snapshots, we ensure backend implementations are
semantically equivalent.

## Architecture

```
                         ReplayCase (JSONL fixture)
                               │
                               ▼
          ┌────────────────────┼────────────────────┐
          ▼                    ▼                    ▼
   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
   │  InMemory     │   │  SQLite      │   │  Redis        │
   │  Session Svc  │   │  Session Svc │   │  Session Svc  │
   │  Memory Svc   │   │  Memory Svc  │   │  Memory Svc   │
   └──────┬───────┘   └──────┬───────┘   └──────┬───────┘
          │                  │                  │
          ▼                  ▼                  ▼
   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
   │ Snapshot A   │   │ Snapshot B   │   │ Snapshot C   │
   └──────┬───────┘   └──────┬───────┘   └──────┬───────┘
          │                  │                  │
          └──────────────────┼──────────────────┘
                             │
                             ▼
                    ┌─────────────────────┐
                    │  Normalizer         │
                    │  (strip IDs,        │
                    │   timestamps, etc.) │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │  recursive_diff()   │
                    │  A vs B vs C        │
                    └──────────┬──────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │  0 unallowed diffs  │
                    │  = PASS ✅          │
                    └─────────────────────┘
```

## Module Structure

```
tests/sessions/replay_consistency/
├── __init__.py           # Package docstring
├── cases.py              # 10 ReplayCase definitions + JSONL fixture load/save
├── fixtures/             # 10 .jsonl fixture files (case_001 ~ case_010)
│   ├── case_001_single_turn.jsonl
│   ├── case_002_multi_turn.jsonl
│   ├── case_003_tool_call.jsonl
│   ├── case_004_state_updates.jsonl
│   ├── case_005_memory_rw.jsonl
│   ├── case_006_summary.jsonl
│   ├── case_007_summary_truncation.jsonl
│   ├── case_008_track_events.jsonl
│   ├── case_009_concurrent_writes.jsonl
│   └── case_010_error_recovery.jsonl
├── normalizer.py         # Event/snapshot field normalization
├── comparator.py         # Recursive diff engine + DiffEntry
├── test_normalizer.py    # 15 unit tests
├── test_comparator.py    # 15 unit tests
└── test_cases.py         # 9 fixture validation tests

tests/sessions/
├── test_replay_consistency.py  # E2E: InMemory vs SQLite (4 tests)
└── test_replay_redis.py        # Redis backend (env-var gated, 3 tests)
```

## Design Decisions

### 1. Normalization Before Comparison

Auto-generated fields (timestamps, invocation IDs, internal counters) differ
between backends. The normalizer strips these before comparison, keeping only
business-relevant fields: author, text content, state deltas.

### 2. JSONL Fixture Format

Each replay case is persisted as a JSONL file where each line is a typed step
(case_header, event, memory_write, memory_query, summary_step, track_event).
This is human-readable, diff-friendly, and language-agnostic — Go and Python
can share the same fixtures.

### 3. Backend Gating via Environment Variables

Redis tests are gated behind the `REDIS_URL` environment variable. If Redis is
unavailable, the tests gracefully skip rather than failing. InMemory and SQLite
tests always run (SQLite uses `:memory:` mode).

### 4. Recursive Diff with Allowed Diffs

The `recursive_diff()` function traverses the entire snapshot tree (dicts,
lists, primitives). `DiffEntry.allowed` tracks which differences are expected
(e.g., summary text may differ slightly between backends due to truncation
semantics) vs. those that indicate a real bug.

### 5. Alignment with Go Reference

The data types (`EventSpec`, `MemoryWriteSpec`, `ReplayCase`, etc.) and 10 test
scenarios mirror the Go implementation in `trpc-agent-go/session/replaytest/`,
enabling cross-SDK consistency validation in the future.

## Replay Execution Flow

```
For each ReplayCase:
  1. Create session (app_name, user_id, session_id)
  2. Apply initial_state
  3. For each EventSpec: append event to session
  4. For each MemoryWriteSpec: store memory entry
  5. For each MemoryQuerySpec: search memory
  6. For each SummaryStep (at after_event_index): trigger summary
  7. For each TrackEventSpec: record track event
  8. Call normalize_snapshot() → output snapshot for comparison
```

## 10 Replay Cases

| # | Case Name | What It Tests |
|---|-----------|---------------|
| 1 | single_turn_text | Basic conversation + single memory write/query |
| 2 | multi_turn_state_updates | Multi-turn dialogue + multiple memory entries |
| 3 | tool_call_roundtrip | Tool call → tool response → assistant reply cycle |
| 4 | scoped_state_overwrite | State delta updates across turns |
| 5 | memory_multi_author_search | Memory search with multiple queries |
| 6 | summary_generation | Forced summary trigger after N events |
| 7 | summary_with_truncation | Multi-filter-key summary with truncation |
| 8 | track_events | Track event recording and retrieval |
| 9 | concurrent_out_of_order_writes | Parallel task simulation |
| 10 | error_recovery | Duplicate messages, duplicate memory entries |

## Dependencies

- `trpc_agent_sdk.events.Event` — event type
- `trpc_agent_sdk.sessions._session.Session` — session type
- `trpc_agent_sdk.sessions.InMemorySessionService` — in-memory backend
- `trpc_agent_sdk.sessions.SqlSessionService` — SQLite backend
- `trpc_agent_sdk.memory.InMemoryMemoryService` — in-memory memory
- `trpc_agent_sdk.memory.SqlMemoryService` — SQLite memory
- `trpc_agent_sdk.memory.RedisMemoryService` — Redis memory (optional)
- Standard library: `dataclasses`, `json`, `typing`
