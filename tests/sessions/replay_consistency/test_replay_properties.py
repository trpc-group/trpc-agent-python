# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

"""Fixed-seed metamorphic replay properties."""

from __future__ import annotations

import asyncio
from dataclasses import asdict

import pytest

from .adapters import InMemoryReplayAdapter
from .canonicalize import canonicalize_snapshot
from .cases import ReplayCase
from .cases import ReplayOp
from .cases import standard_cases


def _run(coro):
    return asyncio.run(coro)


@pytest.mark.replay_property
def test_replay_is_deterministic_for_fixed_cases():
    for case in standard_cases()[:10]:
        first = _run(_run_in_memory(case))
        second = _run(_run_in_memory(case))
        assert canonicalize_snapshot(first) == canonicalize_snapshot(second)


@pytest.mark.replay_property
def test_replay_case_operation_serialization_round_trip():
    for case in standard_cases():
        encoded = [asdict(op) for op in case.operations]
        decoded = tuple(ReplayOp(**item) for item in encoded)
        restored = ReplayCase(
            case_id=case.case_id,
            operations=decoded,
            checkpoints=case.checkpoints,
            expected_entities=case.expected_entities,
            description=case.description,
        )
        assert restored == case


@pytest.mark.replay_property
def test_unrelated_session_operations_do_not_change_target_session():
    case = next(case for case in standard_cases() if case.case_id == "single_turn_text")
    unrelated = (
        ReplayOp(kind="create_session", app_name="other_app", user_id="other_user", session_id="other_session"),
        ReplayOp(
            kind="append_text",
            app_name="other_app",
            user_id="other_user",
            session_id="other_session",
            client_event_id="other_event",
            text="unrelated text",
        ),
    )
    mixed = ReplayCase(
        case_id=case.case_id,
        operations=case.operations + unrelated,
        description="Target replay with unrelated app/user/session operations appended.",
    )

    target = _target_session(canonicalize_snapshot(_run(_run_in_memory(case))))
    mixed_target = _target_session(canonicalize_snapshot(_run(_run_in_memory(mixed))))

    assert mixed_target == target


async def _run_in_memory(case: ReplayCase):
    adapter = InMemoryReplayAdapter(case=case)
    await adapter.setup()
    try:
        return await adapter.replay()
    finally:
        await adapter.close()


def _target_session(snapshot: dict):
    return next(session for session in snapshot["sessions"] if session["app_name"] == "replay_app")
