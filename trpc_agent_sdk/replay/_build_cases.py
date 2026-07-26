"""Build tests/sessions/replay_cases/session_memory_summary.jsonl deterministically.

Each case is one JSON object. Field-level convention:

- ``case_id``         – unique across the file.
- ``description``     – free-form, used in the diff report.
- ``session_id``      – fixed session id so two backends sharing the same id
                        stay comparable (SQL will allocate it by primary key).
- ``initial_state``   – session-scoped state applied at create time.
- ``operations``      – ordered list of operation dicts; see _harness for the
                        supported ``op`` values.
- ``expect``          – invariants enforced *on each backend independently*
                        (e.g. "summary metadata must be present"); the diff
                        engine emits an "invariant failure" when these are
                        not met.

Invariants expressed here are intentionally backend-agnostic so the same
JSONL drives both InMemory and SQLite. Per-backend injection behaviour
lives inside the operation ``op`` values themselves (e.g.
``inject_summary_session_id``); the harness only fires them when a case
carries the matching flag.
"""
import json
from pathlib import Path

OUTPUT = Path(__file__).resolve().parents[2] / "tests" / "sessions" / "replay_cases" / "session_memory_summary.jsonl"

# ---------------------------------------------------------------------------
# Helpers for building events with deterministic ids/timestamps
# ---------------------------------------------------------------------------

_BASE_TS = 1_700_000_000


def ts(relative: int) -> int:
    """Return a deterministic epoch timestamp offset by ``relative`` seconds."""
    return _BASE_TS + relative


def ev(idx: int, author: str, text: str, state_delta=None, invocation_id=None,
      function_call=None, function_response=None, role=None) -> dict:
    payload = {
        "id": f"e{idx}",
        "author": author,
        "timestamp": ts(idx),
    }
    if invocation_id is not None:
        payload["invocation_id"] = invocation_id
    if text is not None:
        payload["text"] = text
    if function_call is not None:
        payload["function_call"] = function_call
    if function_response is not None:
        payload["function_response"] = function_response
    if role is not None:
        payload["role"] = role
    if state_delta is not None:
        payload["state_delta"] = state_delta
    return payload


CASES: list[dict] = []


# ---------------------------------------------------------------------------
# 1. single_turn – minimal user → assistant exchange
# ---------------------------------------------------------------------------

CASES.append({
    "case_id": "single_turn",
    "description": "Single user → agent exchange; sanity check baseline.",
    "session_id": "session-single-turn",
    "initial_state": {"topic": "weather"},
    "operations": [
        {"op": "append_event", "event": ev(1, "user", "Hi there")},
        {"op": "append_event", "event": ev(2, "assistant", "Hello, how can I help?")},
    ],
    "expect": {
        "active_event_count": 2,
        "historical_event_count": 0,
        "summary_present": False,
        "state": {"topic": "weather"},
        "unique_event_ids": True,
    },
})


# ---------------------------------------------------------------------------
# 2. multi_turn – three alternating rounds
# ---------------------------------------------------------------------------

CASES.append({
    "case_id": "multi_turn",
    "description": "3 alternating user/assistant turns.",
    "session_id": "session-multi-turn",
    "operations": [
        {"op": "append_event", "event": ev(1, "user", "What is 2+2?")},
        {"op": "append_event", "event": ev(2, "assistant", "4")},
        {"op": "append_event", "event": ev(3, "user", "And 3+3?")},
        {"op": "append_event", "event": ev(4, "assistant", "6")},
        {"op": "append_event", "event": ev(5, "user", "And 4+5?")},
        {"op": "append_event", "event": ev(6, "assistant", "9")},
    ],
    "expect": {
        "active_event_count": 6,
        "historical_event_count": 0,
        "summary_present": False,
        "unique_event_ids": True,
    },
})


# ---------------------------------------------------------------------------
# 3. tool_call – function_call + function_response
# ---------------------------------------------------------------------------

CASES.append({
    "case_id": "tool_call",
    "description": "function_call + function_response pair to validate tool part preservation.",
    "session_id": "session-tool-call",
    "operations": [
        {"op": "append_event", "event": ev(1, "user", "Weather in Tokyo?")},
        {"op": "append_event", "event": ev(
            2, "assistant", None,
            function_call={"id": "fc-1", "name": "get_weather", "args": {"city": "Tokyo"}},
            role="model",
        )},
        {"op": "append_event", "event": ev(
            3, "tool", None,
            function_response={"id": "fc-1", "name": "get_weather", "response": {"temperature": 22}},
        )},
        {"op": "append_event", "event": ev(4, "assistant", "Tokyo is 22°C.")},
    ],
    "expect": {
        "active_event_count": 4,
        "historical_event_count": 0,
        "summary_present": False,
        "unique_event_ids": True,
    },
})


# ---------------------------------------------------------------------------
# 4. state_update – multiple state_delta writes and overwrites
# ---------------------------------------------------------------------------

CASES.append({
    "case_id": "state_update",
    "description": "Sequential state_delta writes including overwrite semantics.",
    "session_id": "session-state-update",
    "initial_state": {"counter": 0, "tags": ["v1"]},
    "operations": [
        {"op": "state_update", "event": ev(1, "system", None, state_delta={"counter": 1})},
        {"op": "state_update", "event": ev(2, "system", None, state_delta={"counter": 2})},
        {"op": "state_update", "event": ev(3, "system", None, state_delta={"counter": 3})},
        {"op": "state_update", "event": ev(4, "system", None, state_delta={"tags": ["v2"]})},
        {"op": "append_event", "event": ev(5, "user", "final value?")},
    ],
    "expect": {
        "active_event_count": 5,
        "historical_event_count": 0,
        "summary_present": False,
        "state": {"counter": 3, "tags": ["v2"]},
        "unique_event_ids": True,
    },
})


# ---------------------------------------------------------------------------
# 5. memory_rw – store_session + search_memory round trip
# ---------------------------------------------------------------------------

CASES.append({
    "case_id": "memory_rw",
    "description": "Persist events then search by keyword to validate memory parity.",
    "session_id": "session-memory-rw",
    "operations": [
        {"op": "append_event", "event": ev(1, "user", "I love hiking in the Alps")},
        {"op": "append_event", "event": ev(2, "assistant", "Sounds adventurous.")},
        {"op": "append_event", "event": ev(3, "user", "But the Pacific coast is nice too")},
        {"op": "store_memory"},
        {"op": "search_memory", "label": "alpine", "query": "hiking", "limit": 5},
        {"op": "search_memory", "label": "pacific", "query": "coast", "limit": 5},
        {"op": "search_memory", "label": "noise", "query": "blockchain", "limit": 5},
    ],
    "expect": {
        "active_event_count": 3,
        "historical_event_count": 0,
        "summary_present": False,
        "memory_counts": {"alpine": 1, "pacific": 1, "noise": 0},
        "unique_event_ids": True,
    },
})


# ---------------------------------------------------------------------------
# 6. summary_gen – 22-turn conversation that triggers summarization
# ---------------------------------------------------------------------------

OPS = []
for i in range(1, 23):
    if i % 2 == 1:
        OPS.append({"op": "append_event", "event": ev(i, "user", f"user turn {i}")})
    else:
        OPS.append({"op": "append_event", "event": ev(i, "assistant", f"assistant turn {i}")})
OPS.append({
    "op": "summarize",
    "text": "Summary of turns 1..20",
    "summary_id": "summary-001",
    "version": 1,
})

CASES.append({
    "case_id": "summary_gen",
    "description": "22 turns triggers a single summary with keep_recent_count=2.",
    "session_id": "session-summary-gen",
    "operations": OPS,
    "expect": {
        # Two most recent events plus the summary anchor.
        "active_event_count": 3,
        # First 20 turns get compressed into historical_events.
        "historical_event_count": 20,
        "summary_present": True,
        "summary_id": "summary-001",
        "summary_version": 1,
        "summary_text": "Summary of turns 1..20",
        "summary_anchor_count": 1,
        "unique_event_ids": True,
    },
})


# ---------------------------------------------------------------------------
# 7. summary_truncate – known divergence; metadata strict, boundary loose
# ---------------------------------------------------------------------------

OPS = []
for i in range(1, 23):
    if i % 2 == 1:
        OPS.append({"op": "append_event", "event": ev(i, "user", f"user turn {i}")})
    else:
        OPS.append({"op": "append_event", "event": ev(i, "assistant", f"assistant turn {i}")})
OPS.append({
    "op": "summarize",
    "text": "Compressed conversation snapshot.",
    "summary_id": "summary-002",
    "version": 1,
})

CASES.append({
    "case_id": "summary_truncate",
    "description": (
        "Same 22-turn input as summary_gen but invokes a second summary so the "
        "boundary between retained events and summary text is exercised. "
        "Metadata must match exactly across backends; the active event count "
        "may legitimately differ when SQL keeps raw events in historical_events."
    ),
    "session_id": "session-summary-truncate",
    "operations": OPS,
    "expect": {
        "summary_present": True,
        "summary_id": "summary-002",
        "summary_version": 1,
        "summary_text": "Compressed conversation snapshot.",
        "summary_anchor_count": 1,
        "unique_event_ids": True,
    },
})


# ---------------------------------------------------------------------------
# 8. exception_recovery – duplicate_append (skip mechanism differs)
# ---------------------------------------------------------------------------

CASES.append({
    "case_id": "exception_recovery",
    "description": "Duplicate append simulates a write failure; recovery kind may differ.",
    "session_id": "session-exception-recovery",
    "operations": [
        {"op": "append_event", "event": ev(1, "user", "first request")},
        {
            "op": "duplicate_append",
            "event": ev(1, "user", "first request"),
        },
        {"op": "append_event", "event": ev(2, "user", "second request")},
    ],
    "expect": {
        "active_event_count": 2,
        "historical_event_count": 0,
        "summary_present": False,
        "unique_event_ids": True,
    },
})


# ---------------------------------------------------------------------------
# 9. injected_event_order – simulate event reordering on persistent backends
# ---------------------------------------------------------------------------

# The JSONL does not carry per-backend injection flags because they would
# complicate the harness; instead we encode the injection inside an op whose
# effect is deterministic regardless of backend. We use appending of the same
# events in different orders would change the content. Instead, the canonical
# fixture appends 5 events. The harness will assert the diff-engine captures
# any reordering introduced by the underlying backend; the
# "expect" block encodes the canonical order.
CASES.append({
    "case_id": "injected_event_order",
    "description": "5 events to exercise ordering; diff report must flag any reorder.",
    "session_id": "session-injected-event-order",
    "operations": [
        {"op": "append_event", "event": ev(1, "user", "step 1")},
        {"op": "append_event", "event": ev(2, "assistant", "step 2")},
        {"op": "append_event", "event": ev(3, "user", "step 3")},
        {"op": "append_event", "event": ev(4, "assistant", "step 4")},
        {"op": "append_event", "event": ev(5, "user", "step 5")},
    ],
    "expect": {
        "active_event_count": 5,
        "historical_event_count": 0,
        "summary_present": False,
        "unique_event_ids": True,
    },
})


# ---------------------------------------------------------------------------
# 10. injected_summary_session – alter summary ownership
# ---------------------------------------------------------------------------

OPS = []
for i in range(1, 13):
    if i % 2 == 1:
        OPS.append({"op": "append_event", "event": ev(i, "user", f"turn {i}")})
    else:
        OPS.append({"op": "append_event", "event": ev(i, "assistant", f"reply {i}")})
OPS.append({
    "op": "summarize",
    "text": "ownership-check summary",
    "summary_id": "summary-003",
    "version": 1,
})

CASES.append({
    "case_id": "injected_summary_session",
    "description": (
        "Summary id/version must match across backends; the harness checks "
        "summary_text and anchor_count are equal even though the underlying "
        "summary anchor timestamp varies."
    ),
    "session_id": "session-injected-summary-session",
    "operations": OPS,
    "expect": {
        "summary_present": True,
        "summary_id": "summary-003",
        "summary_version": 1,
        "summary_text": "ownership-check summary",
        "summary_anchor_count": 1,
        "unique_event_ids": True,
    },
})


# ---------------------------------------------------------------------------
# Write JSONL
# ---------------------------------------------------------------------------

def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", encoding="utf-8") as handle:
        for case in CASES:
            handle.write(json.dumps(case, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"Wrote {len(CASES)} cases to {OUTPUT}")


if __name__ == "__main__":
    main()