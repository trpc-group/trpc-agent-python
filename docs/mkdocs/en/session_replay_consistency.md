# Session Replay Consistency

The replay framework runs one Session, Memory, and Summary trajectory against
multiple storage backends and reports semantic differences. It can validate a
single backend against an expected snapshot or compare several backends with a
selected reference.

## Design summary

`ReplayHarness` sends each validated `ReplayCase` through every configured
`ReplayBackend`. A backend combines a Session service, a Memory service, and a
deterministic Summary model. After every operation has run, the harness reloads
the stored data, builds a snapshot, normalizes representation-only differences,
and performs two comparisons: backend versus case expectation, and backend
versus the selected reference backend.

Normalization is intentionally narrow. Text uses Unicode NFKC and whitespace
folding, generated event identifiers receive stable aliases, valid timestamps
become positional tokens, and dictionary key order is irrelevant. Missing or
invalid timestamps, event reordering, state changes, and payload changes remain
visible. Memory result ordering is the only built-in allowed difference, and
each use is recorded for an exact backend and JSON Pointer path.

Summary text is normalized and then compared exactly. Summary ownership,
logical identifier, version, update ordering, and replacement chain are strict
metadata, so summary loss, stale overwrite, and cross-session association cannot
be hidden by normalization. InMemory can run alone, lightweight mode pairs it
with file-backed SQLite, and SQL or Redis services can be selected through
backend factories or direct dependency injection. The run result contains both
a serializable report and normalized per-backend snapshots for further analysis.

## Core API

All replay types are available from `trpc_agent_sdk.sessions`.

| Type | Responsibility |
|------|----------------|
| `ReplayCase` | Load and validate one JSONL trajectory and its expected snapshot |
| `ReplayBackend` | Bind Session, Memory, Summary, cleanup, and lifecycle behavior |
| `ReplaySummaryModel` | Supply deterministic Summary text to the normal summarization flow |
| `ReplayHarness` | Execute cases across backends and coordinate comparisons |
| `ReplayNormalizer` | Normalize non-business storage representations |
| `ReplayComparator` | Produce recursive field-level differences |
| `BackendReplayResult` | Retain one backend's raw snapshot, normalized snapshot, and diagnostics |
| `ReplayRunResult` | Return the report and per-backend replay snapshots |
| `ReplayReport` | Serialize a report deterministically as UTF-8 JSON |

## Basic usage

```python
from pathlib import Path

from trpc_agent_sdk.sessions import ReplayHarness
from trpc_agent_sdk.sessions import ReplayReport


async def run_replay() -> None:
    cases = ReplayHarness.load_cases(Path("replay_cases"))
    harness = ReplayHarness.create_lightweight(work_dir=Path(".replay"))
    result = await harness.run(cases)
    ReplayReport.write(result.report, Path("replay_report.json"))
```

`create_in_memory()` creates a single-backend harness. `create_lightweight()`
uses InMemory as the reference and a file-backed SQLite database as the second
backend.

## Backend composition

Use `create_integration()` when connection URLs are already available:

```python
from trpc_agent_sdk.sessions import ReplayHarness


async def compare_remote_backends(sql_url: str, redis_url: str, cases) -> dict:
    harness = ReplayHarness.create_integration(
        sql_url=sql_url,
        redis_url=redis_url,
    )
    result = await harness.run(cases)
    return result.report
```

For a custom composition, construct `ReplayBackend` instances and pass them to
`ReplayHarness`:

```python
from trpc_agent_sdk.sessions import ReplayBackend
from trpc_agent_sdk.sessions import ReplayHarness


def create_replay_harness(sql_url: str) -> ReplayHarness:
    return ReplayHarness(
        backends=[
            ReplayBackend.in_memory(),
            ReplayBackend.sql(sql_url, name="primary_sql"),
        ],
        reference_backend="in_memory",
    )
```

`ReplayBackend` also accepts configured Session and Memory service instances.
Set `cleanup_data=False` when the caller owns persisted replay records, and
`close_services=False` when the caller owns service lifecycles. By default,
`run()` initializes every backend and, in a `finally` block, removes touched
records and closes services. Cleanup targets only the application, user,
session, state, Memory, and Redis keys recorded by that backend.

## Replay DSL

Each JSONL file starts with a `case` record. Later lines contain operations:

| Operation | Behavior |
|-----------|----------|
| `create_session` | Create a named Session with optional initial state |
| `append_event` | Append a complete SDK `Event`, including tool and state data |
| `store_memory` | Store the current Session through the Memory service |
| `search_memory` | Capture a named Memory query result |
| `summarize` | Run Session summarization and persist replay Summary metadata |
| `inject_failure` | Simulate a declared partial write or repeated submission |
| `checkpoint` | Record a logical boundary in the resulting snapshot |

The first record supplies `case_id` and `expected`. Operation records use unique
`operation_id` values and reference Sessions created earlier in the trajectory.
The loader validates required fields, event structure, identifier uniqueness,
Summary versions, replacement links, and increasing Summary update times.

## Snapshot and normalization

A snapshot contains:

- Sessions, active events, and historical events;
- merged Session, User, and App state;
- named Memory query results;
- Summary text and replay metadata;
- checkpoints and operation errors.

Text normalization applies recursively to message text, tool payloads, Memory,
and Summary content. Automatic event IDs are mapped to stable aliases.
Timestamp tokens preserve presence and relative list position; a missing,
malformed, or decreasing timestamp remains a difference. Dictionary comparison
is recursive and independent of serialized key order.

Memory implementations may return equally relevant results in different orders.
When canonical sorting changes that order, the backend result records an
`allowed_diff` containing the backend name, JSON Pointer, original order, and
normalization strategy. No wildcard field exclusion is applied.

## Summary semantics

The deterministic model supplies the Summary text declared by the replay
operation, while `SessionSummarizer` and `SummarizerSessionManager` perform the
normal compression and persistence flow. Replay metadata stores:

- `summary_id`;
- owning `session_id`;
- `version`;
- `updated_at`;
- `replaces_summary_id`.

Text is compared after NFKC and whitespace normalization. The metadata fields
remain exact. Updated Summaries must advance the version and point to the
Summary they replace, allowing the comparator to distinguish a valid replacement
from a stale overwrite.

## Report and differences

Each backend is compared with the partial expected snapshot using subset
semantics. Non-reference backends are also compared with the full normalized
reference snapshot. A difference includes:

- backend and reference backend;
- Session ID when the path belongs to a Session;
- JSON Pointer field path;
- reference and backend values;
- event index, Memory query/index, or Summary ID when applicable.

`ReplayRunResult.report` is ready for stable JSON serialization.
`ReplayRunResult.backend_results` retains raw and normalized snapshots,
normalization records, allowed differences, and operation errors for
programmatic inspection.
