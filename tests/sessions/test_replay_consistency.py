"""Replay consistency tests for Session / Memory / Summary backends."""

from __future__ import annotations

from dataclasses import asdict
import copy
import json
from pathlib import Path
from typing import Any

import pytest

from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import EventActions
from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import FunctionResponse
from trpc_agent_sdk.types import MemoryEntry
from trpc_agent_sdk.types import Part
from trpc_agent_sdk.types import State

from .replay_consistency.backends import BackendBundle
from .replay_consistency.backends import build_backends
from .replay_consistency.backends import make_session_config
from .replay_consistency.cases import EventSpec
from .replay_consistency.cases import MemoryQuerySpec
from .replay_consistency.cases import ReplayCase
from .replay_consistency.cases import replay_cases
from .replay_consistency.comparator import DiffEntry
from .replay_consistency.comparator import compare_snapshot_pair
from .replay_consistency.comparator import recursive_diff
from .replay_consistency.comparator import unallowed_diffs
from .replay_consistency.normalizer import Snapshot
from .replay_consistency.normalizer import normalize_snapshot
from .replay_consistency.report import write_report


REQUIRED_CASE_NAMES = [
    "single_turn_text",
    "multi_turn_append_order",
    "tool_call_roundtrip",
    "scoped_state_overwrite",
    "memory_preference_search",
    "memory_multi_session_isolation",
    "summary_generation",
    "summary_update_overwrite",
    "summary_with_event_truncation",
    "duplicate_or_error_recovery",
]

FIXED_EVENT_TIMESTAMP_BASE = 2_000_000_000.0


def _event_from_spec(spec: EventSpec, index: int) -> Event:
    parts: list[Part] = []
    if spec.text is not None:
        parts.append(Part.from_text(text=spec.text))
    if spec.function_call is not None:
        parts.append(
            Part(
                function_call=FunctionCall(
                    id=spec.function_call.get("id"),
                    name=spec.function_call["name"],
                    args=spec.function_call.get("args") or {},
                )
            )
        )
    if spec.function_response is not None:
        parts.append(
            Part(
                function_response=FunctionResponse(
                    id=spec.function_response.get("id"),
                    name=spec.function_response["name"],
                    response=spec.function_response.get("response") or {},
                )
            )
        )

    return Event(
        id=spec.event_id,
        invocation_id=spec.invocation_id,
        author=spec.author,
        content=Content(role=spec.role, parts=parts),
        actions=EventActions(state_delta=copy.deepcopy(spec.state_delta or {})),
        branch=spec.branch,
        tag=spec.tag,
        filter_key=spec.filter_key,
        partial=spec.partial,
        error_code=spec.error_code,
        error_message=spec.error_message,
        timestamp=FIXED_EVENT_TIMESTAMP_BASE + index,
    )


async def _get_required_session(bundle: BackendBundle, case: ReplayCase) -> Session:
    stored = await bundle.session_service.get_session(
        app_name=case.app_name,
        user_id=case.user_id,
        session_id=case.session_id,
    )
    if stored is None:
        pytest.fail(f"{bundle.name} did not return stored session {case.session_id}")
    return stored


async def _create_required_summary(bundle: BackendBundle, session: Session, case: ReplayCase) -> str:
    await bundle.session_service.create_session_summary(session)
    summary_text = await bundle.session_service.get_session_summary(session)
    if summary_text is None:
        pytest.fail(f"{bundle.name} did not create a summary for {case.name}/{session.id}")
    if not summary_text.strip():
        pytest.fail(f"{bundle.name} created an empty summary for {case.name}/{session.id}")
    return summary_text


async def _search_memory_records(
    bundle: BackendBundle,
    session: Session,
    memory_queries: list[MemoryQuerySpec],
) -> list[tuple[MemoryQuerySpec, str, list[MemoryEntry]]]:
    records: list[tuple[MemoryQuerySpec, str, list[MemoryEntry]]] = []
    for query_spec in memory_queries:
        key = query_spec.key or session.save_key
        response = await bundle.memory_service.search_memory(key=key, query=query_spec.query, limit=query_spec.limit)
        memory_texts = "\n".join(
            "".join(part.text for part in memory.content.parts if part.text)
            for memory in response.memories
            if memory.content and memory.content.parts
        )
        for expected in query_spec.expected_text_fragments:
            if expected not in memory_texts:
                pytest.fail(
                    f"{bundle.name} memory query {query_spec.query!r} for {session.id} "
                    f"did not return expected fragment {expected!r}; got {memory_texts!r}"
                )
        records.append((query_spec, key, response.memories))
    return records


def _temp_state_keys(state: dict[str, Any] | None) -> list[str]:
    if not state:
        return []
    return sorted(key for key in state if key.startswith(State.TEMP_PREFIX))


def _assert_no_persisted_temp_state(bundle: BackendBundle, case: ReplayCase, session: Session) -> None:
    temp_state_keys = _temp_state_keys(session.state)
    if temp_state_keys:
        pytest.fail(
            f"{bundle.name} persisted temp state on {case.name}/{session.id}: {temp_state_keys}"
        )

    temp_delta_keys: list[tuple[str | None, str]] = []
    for event in [*session.events, *session.historical_events]:
        if event.actions:
            temp_delta_keys.extend((event.id, key) for key in _temp_state_keys(event.actions.state_delta))
    if temp_delta_keys:
        pytest.fail(
            f"{bundle.name} persisted temp state_delta keys on {case.name}/{session.id}: {temp_delta_keys}"
        )

    if case.name == "scoped_state_overwrite":
        fixture_temp_keys = [
            key
            for spec in case.events
            for key in _temp_state_keys(spec.state_delta)
        ]
        if not fixture_temp_keys:
            pytest.fail("scoped_state_overwrite no longer exercises temp state filtering")


def _assert_summary_snapshot(snapshot: Snapshot, case: ReplayCase) -> None:
    if not case.summary_points:
        return
    if snapshot.summary is None:
        pytest.fail(f"{snapshot.backend} summary snapshot is missing for {case.name}")

    summary = snapshot.summary
    metadata = summary["metadata"]
    if not summary["text"]:
        pytest.fail(f"{snapshot.backend} summary text is empty for {case.name}")
    if metadata["session_id"] != snapshot.session_id:
        pytest.fail(f"{snapshot.backend} summary session mismatch for {case.name}")
    if metadata["summary_event_count"] < 1:
        pytest.fail(f"{snapshot.backend} summary event missing for {case.name}")
    event_text = metadata["summary_event_text"]
    if not event_text or not event_text.startswith("Previous conversation summary:"):
        pytest.fail(f"{snapshot.backend} summary event text missing prefix for {case.name}")

    summary_events = [event for event in snapshot.events if event["is_summary_event"]]
    if not summary_events:
        pytest.fail(f"{snapshot.backend} summary event flag missing for {case.name}")
    if summary_events[0]["author"] != "system":
        pytest.fail(f"{snapshot.backend} summary event author is not system for {case.name}")

    if case.name == "summary_with_event_truncation":
        if snapshot.events[0]["is_summary_event"] is not True:
            pytest.fail(f"{snapshot.backend} truncation case did not keep summary event first")
        if metadata["historical_event_count"] == 0 or not snapshot.historical_events:
            pytest.fail(f"{snapshot.backend} truncation case did not persist historical events")
        if snapshot.events[-1]["text"] != "Also add a ferry ride after the summary.":
            pytest.fail(f"{snapshot.backend} truncation case lost post-summary append")


def _assert_duplicate_error_recovery_snapshot(snapshot: Snapshot) -> None:
    if snapshot.case_name != "duplicate_or_error_recovery":
        return
    error_events = [
        event
        for event in snapshot.events
        if event["error_code"] == "RETRYABLE_BACKEND_ERROR"
        and event["error_message"] == "Simulated retry failure before recovery."
    ]
    if len(error_events) != 1:
        pytest.fail(f"{snapshot.backend} did not preserve the retry error event")
    if error_events[0]["event_id"] != "duplicate_or_error_recovery-event-03":
        pytest.fail(f"{snapshot.backend} did not preserve the deterministic error event id")
    if not any(event["text"] == "Recovery succeeded after retry." for event in snapshot.events):
        pytest.fail(f"{snapshot.backend} did not preserve the recovery event")


def _assert_fixture_event_ids_preserved(snapshot: Snapshot, case: ReplayCase) -> None:
    expected_event_ids = {spec.event_id for spec in case.events}
    for event in [*snapshot.events, *snapshot.historical_events]:
        if event["is_summary_event"]:
            continue
        if event["event_id"] not in expected_event_ids:
            pytest.fail(
                f"{snapshot.backend} did not preserve deterministic event id for "
                f"{case.name}: {event['event_id']!r}"
            )


async def _run_standard_case(bundle: BackendBundle, case: ReplayCase) -> Snapshot:
    summary_texts: list[str] = []
    session = await bundle.session_service.create_session(
        app_name=case.app_name,
        user_id=case.user_id,
        session_id=case.session_id,
        state=copy.deepcopy(case.initial_state),
    )
    for index, spec in enumerate(case.events):
        event = _event_from_spec(spec, index)
        await bundle.session_service.append_event(session, event)
        if index in case.summary_points:
            summary_texts.append(await _create_required_summary(bundle, session, case))
            session = await _get_required_session(bundle, case)

    stored_session = await _get_required_session(bundle, case)
    _assert_no_persisted_temp_state(bundle, case, stored_session)
    if case.name == "summary_update_overwrite" and len(summary_texts) >= 2 and summary_texts[0] == summary_texts[-1]:
        pytest.fail(f"{bundle.name} did not overwrite the cached summary for {case.name}")

    await bundle.memory_service.store_session(stored_session)
    memory_records = await _search_memory_records(bundle, stored_session, case.memory_queries)
    snapshot = await normalize_snapshot(
        backend=bundle.name,
        case=case,
        session=stored_session,
        session_service=bundle.session_service,
        memory_records=memory_records,
    )
    _assert_summary_snapshot(snapshot, case)
    _assert_fixture_event_ids_preserved(snapshot, case)
    _assert_duplicate_error_recovery_snapshot(snapshot)
    return snapshot


async def _run_memory_isolation_case(bundle: BackendBundle, case: ReplayCase) -> Snapshot:
    session_a = await bundle.session_service.create_session(
        app_name=case.app_name,
        user_id=case.user_id,
        session_id=case.session_id,
        state=copy.deepcopy(case.initial_state),
    )
    for index, spec in enumerate(case.events):
        await bundle.session_service.append_event(session_a, _event_from_spec(spec, index))

    session_b = await bundle.session_service.create_session(
        app_name=case.app_name,
        user_id="user-isolation-b",
        session_id="session-006-b",
        state={},
    )
    other_specs = [
        EventSpec(
            event_id="memory_multi_session_isolation-b-event-00",
            invocation_id="inv-isolation-b-1",
            author="user",
            role="user",
            text="User B also likes jasmine tea and hiking, but only with city-museums-b.",
            function_call=None,
            function_response=None,
            state_delta=None,
            branch=None,
            tag=None,
            filter_key=None,
        ),
        EventSpec(
            event_id="memory_multi_session_isolation-b-event-01",
            invocation_id="inv-isolation-b-1",
            author="assistant",
            role="model",
            text="I will remember jasmine tea, hiking, and city-museums-b for User B.",
            function_call=None,
            function_response=None,
            state_delta=None,
            branch=None,
            tag=None,
            filter_key=None,
        ),
    ]
    for index, spec in enumerate(other_specs, start=100):
        await bundle.session_service.append_event(session_b, _event_from_spec(spec, index))

    stored_a = await _get_required_session(bundle, case)
    stored_b = await bundle.session_service.get_session(
        app_name=case.app_name,
        user_id="user-isolation-b",
        session_id="session-006-b",
    )
    if stored_b is None:
        pytest.fail(f"{bundle.name} did not return isolation control session")
    _assert_no_persisted_temp_state(bundle, case, stored_a)
    _assert_no_persisted_temp_state(bundle, case, stored_b)

    await bundle.memory_service.store_session(stored_a)
    await bundle.memory_service.store_session(stored_b)
    memory_records = await _search_memory_records(bundle, stored_a, case.memory_queries)
    leaked_text = "\n".join(
        "".join(part.text for part in memory.content.parts if part.text)
        for _, _, memories in memory_records
        for memory in memories
        if memory.content and memory.content.parts
    )
    if "city-museums-b" in leaked_text:
        pytest.fail(f"{bundle.name} leaked user B memory into user A search: {leaked_text!r}")

    snapshot = await normalize_snapshot(
        backend=bundle.name,
        case=case,
        session=stored_a,
        session_service=bundle.session_service,
        memory_records=memory_records,
    )
    _assert_fixture_event_ids_preserved(snapshot, case)
    return snapshot


async def run_case(bundle: BackendBundle, case: ReplayCase) -> Snapshot:
    try:
        if case.name == "memory_multi_session_isolation":
            return await _run_memory_isolation_case(bundle, case)
        return await _run_standard_case(bundle, case)
    finally:
        await bundle.close()


def _session_config_for_case(case: ReplayCase):
    return make_session_config(store_historical_events=case.name == "summary_with_event_truncation")


@pytest.mark.asyncio
async def test_replay_consistency_inmemory_vs_sqlite(tmp_path: Path):
    cases = replay_cases()
    assert len(cases) == 10
    comparison_results: list[dict[str, Any]] = []
    report_path = tmp_path / "session_memory_summary_diff_report.json"

    for case in cases:
        case_tmp_path = tmp_path / case.name
        backends = await build_backends(case_tmp_path, session_config=_session_config_for_case(case))
        assert {"inmemory", "sqlite"} <= {backend.name for backend in backends}

        snapshots: dict[str, Snapshot] = {}
        for backend in backends:
            snapshots[backend.name] = await run_case(backend, case)

        left = snapshots["inmemory"]
        for right_name, right in snapshots.items():
            if right_name == "inmemory":
                continue
            diffs = compare_snapshot_pair(left, right)
            comparison_results.append(
                {
                    "case_name": case.name,
                    "left_backend": left.backend,
                    "right_backend": right.backend,
                    "diffs": diffs,
                }
            )
            unexpected = unallowed_diffs(diffs)
            if unexpected:
                write_report(report_path, comparison_results)
                pytest.fail(f"Replay diff detected for {case.name}: {[asdict(diff) for diff in unexpected]}")

    write_report(report_path, comparison_results)


def test_replay_case_count_and_names():
    assert [case.name for case in replay_cases()] == REQUIRED_CASE_NAMES


def test_report_contract(tmp_path: Path):
    diff = DiffEntry(
        case_name="case",
        left_backend="inmemory",
        right_backend="sqlite",
        session_id="session",
        event_index=1,
        memory_index=None,
        summary_id="summary:session:latest",
        section="events",
        path="events[1].text",
        left="hello",
        right="bye",
        allowed=False,
        reason="",
    )
    allowed_diff = DiffEntry(
        case_name="case",
        left_backend="inmemory",
        right_backend="sqlite",
        session_id="session",
        event_index=None,
        memory_index=None,
        summary_id=None,
        section="backend",
        path="backend",
        left="inmemory",
        right="sqlite",
        allowed=True,
        reason="backend name differs by design",
    )
    path = tmp_path / "report.json"
    report = write_report(
        path,
        [
            {
                "case_name": "case",
                "left_backend": "inmemory",
                "right_backend": "sqlite",
                "diffs": [allowed_diff, diff],
            }
        ],
    )
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded == report
    serialized = loaded["unallowed_diffs"][0]
    for field in DiffEntry.__dataclass_fields__:
        assert field in serialized
    assert loaded["schema_version"] == 1
    assert loaded["backend_pairs"] == ["inmemory_vs_sqlite"]
    assert loaded["allowed_diff_count"] == 1
    assert loaded["unallowed_diff_count"] == 1
    assert len(loaded["allowed_diffs"]) == 1
    assert len(loaded["unallowed_diffs"]) == 1
    assert len(loaded["diffs"]) == 2
    assert loaded["cases"][0]["allowed_diff_count"] == 1
    assert loaded["cases"][0]["unallowed_diff_count"] == 1


def _clean_mutation_snapshot() -> dict[str, Any]:
    snapshot = Snapshot(
        backend="inmemory",
        case_name="mutation_fixture",
        session_id="session-mutation",
        app_name="replay-app",
        user_id="user-mutation",
        state={
            "user:tier": "gold",
            "preference": "tea",
        },
        events=[
            {
                "stable_index": 0,
                "event_id": "normalized",
                "invocation_id": "inv-mutation-1",
                "author": "user",
                "role": "user",
                "text": "What is the weather in Beijing?",
                "function_calls": [],
                "function_responses": [],
                "state_delta": {},
                "branch": None,
                "tag": None,
                "filter_key": None,
                "partial": False,
                "turn_complete": False,
                "error_code": None,
                "error_message": None,
                "model_visible": True,
                "is_summary_event": False,
            },
            {
                "stable_index": 1,
                "event_id": "normalized",
                "invocation_id": "inv-mutation-1",
                "author": "assistant",
                "role": "model",
                "text": None,
                "function_calls": [
                    {
                        "id": "call-weather-1",
                        "name": "get_weather",
                        "args": {
                            "city": "Beijing",
                            "unit": "celsius",
                        },
                    }
                ],
                "function_responses": [],
                "state_delta": {},
                "branch": "weather.main",
                "tag": "tool-call",
                "filter_key": "weather",
                "partial": False,
                "turn_complete": False,
                "error_code": None,
                "error_message": None,
                "model_visible": True,
                "is_summary_event": False,
            },
            {
                "stable_index": 2,
                "event_id": "normalized",
                "invocation_id": "inv-mutation-1",
                "author": "assistant",
                "role": "model",
                "text": "Beijing is sunny and 25 celsius.",
                "function_calls": [],
                "function_responses": [],
                "state_delta": {},
                "branch": "weather.main",
                "tag": "final",
                "filter_key": "weather",
                "partial": False,
                "turn_complete": False,
                "error_code": None,
                "error_message": None,
                "model_visible": True,
                "is_summary_event": False,
            },
        ],
        historical_events=[
            {
                "stable_index": 0,
                "event_id": "normalized",
                "invocation_id": "inv-old",
                "author": "user",
                "role": "user",
                "text": "Old preference was captured.",
                "function_calls": [],
                "function_responses": [],
                "state_delta": {},
                "branch": None,
                "tag": None,
                "filter_key": None,
                "partial": False,
                "turn_complete": False,
                "error_code": None,
                "error_message": None,
                "model_visible": True,
                "is_summary_event": False,
            }
        ],
        memories=[
            {
                "query": "tea",
                "key": "replay-app/user-mutation",
                "author": "user",
                "text": "I prefer tea in the morning.",
                "has_timestamp": True,
            }
        ],
        summary={
            "text": "summary(session-mutation): user=Old preference was captured. | facts=1-events",
            "metadata": {
                "session_id": "session-mutation",
                "has_summary": True,
                "summary_event_count": 1,
                "summary_event_text": (
                    "Previous conversation summary: summary(session-mutation): "
                    "user=Old preference was captured. | facts=1-events"
                ),
                "compressed_event_count": 3,
                "historical_event_count": 1,
                "manager_session_id": "session-mutation",
                "original_event_count": 4,
                "manager_compressed_event_count": 3,
                "has_summary_timestamp": True,
            },
        },
        list_sessions=[
            {
                "id": "session-mutation",
                "app_name": "replay-app",
                "user_id": "user-mutation",
                "state": {
                    "user:tier": "gold",
                    "preference": "tea",
                },
            }
        ],
    )
    return asdict(snapshot)


def _mutate_snapshot(name: str, snapshot: dict[str, Any]) -> None:
    if name == "drop_event":
        del snapshot["events"][1]
    elif name == "reorder_event":
        snapshot["events"][0], snapshot["events"][1] = snapshot["events"][1], snapshot["events"][0]
    elif name == "change_tool_args":
        snapshot["events"][1]["function_calls"][0]["args"]["city"] = "Shanghai"
    elif name == "change_state":
        snapshot["state"]["user:tier"] = "silver"
    elif name == "drop_memory":
        del snapshot["memories"][0]
    elif name == "change_memory_text":
        snapshot["memories"][0]["text"] = "I prefer coffee in the morning."
    elif name == "drop_summary":
        snapshot["summary"] = None
    elif name == "overwrite_summary_text":
        snapshot["summary"]["text"] = "summary(session-mutation): overwritten"
    elif name == "wrong_summary_session":
        snapshot["summary"]["metadata"]["session_id"] = "wrong-session"
    elif name == "duplicate_event":
        snapshot["events"].append(copy.deepcopy(snapshot["events"][0]))
    else:
        raise ValueError(f"Unknown mutation {name}")


MUTATIONS = [
    "drop_event",
    "reorder_event",
    "change_tool_args",
    "change_state",
    "drop_memory",
    "change_memory_text",
    "drop_summary",
    "overwrite_summary_text",
    "wrong_summary_session",
    "duplicate_event",
]


def _real_snapshot_mutations_for_case(case: ReplayCase) -> list[str]:
    mutations = ["drop_event", "reorder_event", "duplicate_event"]
    if case.name == "scoped_state_overwrite":
        mutations.append("change_state")
    if case.name in {"memory_preference_search", "memory_multi_session_isolation"}:
        mutations.extend(["drop_memory", "change_memory_text"])
    if case.name in {"summary_generation", "summary_update_overwrite", "summary_with_event_truncation"}:
        mutations.extend(["drop_summary", "overwrite_summary_text", "wrong_summary_session"])
    return mutations


async def _run_real_inmemory_snapshot(tmp_path: Path, case: ReplayCase) -> dict[str, Any]:
    backends = await build_backends(tmp_path, session_config=_session_config_for_case(case))
    selected: BackendBundle | None = None
    unused: list[BackendBundle] = []
    for backend in backends:
        if backend.name == "inmemory":
            selected = backend
        else:
            unused.append(backend)
    if selected is None:
        for backend in unused:
            await backend.close()
        pytest.fail("InMemory replay backend was not built")

    for backend in unused:
        await backend.close()
    return asdict(await run_case(selected, case))


@pytest.mark.asyncio
async def test_real_replay_snapshot_mutation_detection(tmp_path: Path):
    for case in replay_cases():
        clean = await _run_real_inmemory_snapshot(tmp_path / f"real-mutation-{case.name}", case)
        for mutation in _real_snapshot_mutations_for_case(case):
            mutated = copy.deepcopy(clean)
            mutated["backend"] = "mutated"
            _mutate_snapshot(mutation, mutated)
            diffs = recursive_diff(clean, mutated)
            assert unallowed_diffs(diffs), f"{case.name}/{mutation} was not detected"


@pytest.mark.parametrize("mutation", MUTATIONS)
def test_replay_mutation_detection(mutation: str):
    clean = _clean_mutation_snapshot()
    mutated = copy.deepcopy(clean)
    mutated["backend"] = "sqlite"
    _mutate_snapshot(mutation, mutated)
    diffs = recursive_diff(clean, mutated)
    assert unallowed_diffs(diffs), f"{mutation} was not detected"


@pytest.mark.parametrize(
    ("mutation", "expected_path"),
    [
        ("drop_summary", "summary"),
        ("overwrite_summary_text", "summary.text"),
        ("wrong_summary_session", "summary.metadata.session_id"),
    ],
)
def test_summary_required_mutations_detected(mutation: str, expected_path: str):
    clean = _clean_mutation_snapshot()
    mutated = copy.deepcopy(clean)
    mutated["backend"] = "sqlite"
    _mutate_snapshot(mutation, mutated)
    diffs = unallowed_diffs(recursive_diff(clean, mutated))
    assert diffs
    assert any(diff.section == "summary" and diff.path == expected_path for diff in diffs)
