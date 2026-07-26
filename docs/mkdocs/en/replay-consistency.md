# Replay Consistency Playbook

The playbook for replay consistency. One job: **does each pair of
backends that implement `SessionServiceABC` / `MemoryServiceABC`
(InMemory, SQL, Redis) produce the same business snapshot on the same
input?**

This harness runs the same trace on two backends and compares them
field by field. Results land in
`tests/sessions/replay_diff_report.json` — every `differences` entry
is a real divergence tagged with a field path.

The default run:

```bash
pytest tests/sessions/test_replay_consistency.py -v
```

InMemory ⇄ SQLite, about 24 seconds, no external services. Set the
corresponding env vars to bring Redis / MySQL into the matrix; the
integration tests skip cleanly when the vars aren't set.

## The harness is five stages

```
JSONL cases  →  harness  →  normalizer  →  diff  →  report
single source    run on 2    drop non-business   compare by path   write JSON
                backends    fields
```

The harness is five modules under `trpc_agent_sdk/replay/` (plus an
`__init__.py` re-exporting the public surface). To add a backend, edit
exactly one branch in
`trpc_agent_sdk/replay/_backends.py::_build_backend`.

## The 11 replay cases

All live in
[`tests/sessions/replay_cases/session_memory_summary.jsonl`](../../../tests/sessions/replay_cases/session_memory_summary.jsonl),
one case per line, in the same order as the table below:

| # | Case | What it stresses | A divergence means the backend … |
|---|------|------------------|----------------------------------|
| 1 | `single_turn` | one user → agent pair | dropped events or wrong author |
| 2 | `multi_turn` | three alternating rounds | swapped order on append |
| 3 | `tool_call` | function_call + response | Part serialization mismatch |
| 4 | `state_update` | 4 overlapping `state_delta` writes | last-write-wins broken |
| 5 | `memory_rw` | store_session + search_memory | indexer never ran |
| 6 | `summary_gen` | 22 turns → summary | anchor event not written |
| 7 | `summary_truncate` | summary + post-summary appends | compression boundary drift |
| 8 | `exception_recovery` | duplicate append + summary failure | missing compensating logic |
| 9 | `injected_event_order` | two events swapped on one backend | diff engine lost order awareness |
| 10 | `injected_summary_session` | summary `session_id` tampered | cross-session summary leak |
| 11 | `fail_summary_recovery` | summary failure + compensating rollback | failed summary id leaks into cache |

> 9, 10, and 11 are **injected failures**, not realistic data. They exist
> to prove the diff engine catches the bug class it claims to catch. If
> a change to the engine makes any of them pass, you've lost detection
> coverage — fix the engine before relaxing the case.

## Normalization & allowed-diff rules

`NORMALIZATION_RULES` and `ALLOWED_DIFF_RULES` are exported as public
constants. The first defines **automatically dropped** fields; the
second defines **explicitly allowed** differences. Both must be
defended in the commit message — widening either one is a contract
change.

```
dropped:    timestamp → second precision · id reassigned by content
            state_delta key order · is_final_response
allowed:    backend-generated invocation_id · save_key format
            post-compression event count
```

## Summary semantics — three layers

1. **Strict metadata match** across backends: `summary_id`, `session_id`,
   `version`, `text`, `anchor_count`, `original_event_count`,
   `compressed_event_count`. Differences here are the overwhelming
   majority of summary bugs.
2. **Per-backend invariants**: each backend must independently satisfy
   `compressed < original` (compression actually happened), non-empty
   text, and post-summary appends remain readable.
3. **Known-divergence class**: cases tagged
   `known_summary_divergence` in `EXPECTATIONS` allow diffs only in
   `events` / `summary` domains, and each such diff must carry an
   `allowed_diff` justification. A `state` diff here is a real bug.

## Integration mode & CI

```bash
pytest tests/sessions/test_replay_consistency.py -v     # default 24s, local-only
TRPC_REPLAY_REDIS_URL=redis://...  pytest -m integration # enable Redis
TRPC_REPLAY_SQL_URL=mysql+...      pytest -m integration # enable MySQL
```

The `integration_runtime` fixture in `conftest.py` resolves the env
vars + optional deps at run start. Missing either → clean skip with a
reason; never a hard failure.

- `ci.yml` — runs the lightweight suite on every PR, ≤ 30s budget.
- `.github/workflows/replay-integration.yml` — weekly + manual trigger,
  uses `redis:7-alpine` / `mysql:8.0` service containers. **Forks without
  the secret are not hard-failed**: the workflow comments explicitly call
  out that no job-level `if:` guard is used (the `secrets` context is
  unavailable in job-level `if:` expressions), so skipping is handled by
  the `integration_runtime` fixture in `tests/sessions/conftest.py` at
  the test level — those forks see a stream of "skipped" entries instead
  of "job failed". Diff reports upload as workflow artifacts on every run.

## Common failure modes

| Symptom in the report | Likely cause |
|----------------------|--------------|
| `$.events[*].id` differs across backends | new event bypassed `_canonical_event` |
| `$.summary.current.session_id` mismatch | summary was created in the wrong session namespace |
| `$.summary.current.version` mismatch | backend skipped a revision |
| zero memory results on one backend | case forgot to call `store_session` before `search_memory` |
| `duplicate_append` lacks recovery | backend lacks compensating-deduplication; SQL usually needs a unique constraint on `(session_id, event_id)` |
| injected case has empty `differences` | diff engine lost detection — fix the engine, don't relax the case |

## Adding a case or a backend

**A new case** is one JSON line. The `op` field supports
`append_event`, `state_update`, `summarize`, `store_memory`,
`search_memory`, `duplicate_append`, `fail_summary` — see
[`_harness.py::_run_case`](../../../trpc_agent_sdk/replay/_harness.py)
for the schema. After adding the case, register its `case_id` in
`tests/sessions/test_replay_consistency.py`'s `EXPECTATIONS` map with
one of: `normal`, `known_summary_divergence`, `allowed_mechanism_only`.

**A new backend** is five steps: implement `SessionServiceABC` +
`MemoryServiceABC` → add a branch in
[`_backends.py::_build_backend`](../../../trpc_agent_sdk/replay/_backends.py)
→ register the name in `resolve_backend_names` → re-run the
lightweight suite (expect it to fail on first run; the failure tells
you which invariant it missed) → if truly necessary, add an
`ALLOWED_DIFF_RULES` entry with a one-sentence justification.
