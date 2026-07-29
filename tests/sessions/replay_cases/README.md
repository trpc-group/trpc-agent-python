# Replay case catalog

[简体中文](README.zh_CN.md)

This directory contains the standardized JSONL trajectories used by
`tests/sessions/test_replay_consistency.py`. See the
[formal replay documentation](../../../docs/mkdocs/en/session_replay_consistency.md)
for architecture, normalization, comparison, and backend integration details.

## JSONL structure

Each file contains one JSON object per line. The first record is `case`, which
declares the case ID, category, expected anomaly flag, and normalized expected
snapshot. The remaining records are replay operations:

| Operation | Fixture purpose |
|-----------|-----------------|
| `create_session` | Create a session with optional initial state |
| `append_event` | Append a complete SDK event |
| `store_memory` | Store the reloaded session in Memory |
| `search_memory` | Capture a named Memory query result |
| `summarize` | Generate and persist deterministic summary text |
| `inject_failure` | Exercise a declared write or duplicate-submit failure |
| `checkpoint` | Mark a logical validation boundary |

## Public cases

| File | Coverage |
|------|----------|
| `01_single_turn.jsonl` | One user event and one agent text event |
| `02_multi_turn.jsonl` | Three consecutive user/agent turns |
| `03_tool_call.jsonl` | Function call, function response, and final answer |
| `04_state_update.jsonl` | Repeated session, user, app, and temporary state updates |
| `05_memory.jsonl` | Cross-session preference and fact memory |
| `06_summary_create.jsonl` | Initial summary creation and retained events |
| `07_summary_update.jsonl` | Versioned summary replacement |
| `08_summary_truncation.jsonl` | Summary, retained history, and later events |
| `09_write_failure.jsonl` | Failure between Session and Memory persistence |
| `10_duplicate_write.jsonl` | Repeated submission of the same event ID |

## Authoring rules

- Store only standard input trajectories and expected snapshots. Deliberate
  mutations belong in the acceptance test.
- Use unique operation, query, checkpoint, and summary identifiers. Duplicate
  event IDs are allowed only in the declared duplicate-submission case.
- Use increasing event and summary timestamps. Summary updates must increment
  the version and reference the summary they replace.
- Include business-relevant expected state, Memory results, retained and
  historical event IDs, and Summary metadata.

Run the catalog from the repository root:

```bash
python -m pytest tests/sessions/test_replay_consistency.py -p no:cacheprovider
```
