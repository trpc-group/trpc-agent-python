"""Turn and loop benchmark scripts, profiles, and conversation construction."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from trpc_agent_bench.bench._evalset import import_time
from trpc_agent_bench.bench._evalset import loop1
from trpc_agent_bench.bench._evalset import loop5
from trpc_agent_bench.bench._evalset import loop10
from trpc_agent_bench.bench._evalset import memory_footprint
from trpc_agent_bench.bench._evalset import turn1
from trpc_agent_bench.bench._evalset import turn5
from trpc_agent_bench.bench._evalset import turn10
from trpc_agent_bench.bench._evalset._script_fixtures import make_loop_script
from trpc_agent_bench.bench._evalset._script_fixtures import make_turn_script
from trpc_agent_bench.bench._utils._session_bench import _expected_text
from trpc_agent_bench.bench._utils._session_bench import _has_function
from trpc_agent_bench.bench._utils._session_bench import _make_conversation
from trpc_agent_bench.bench._utils._session_bench import _session_user_id
from trpc_agent_bench.core import AgentHandle
from trpc_agent_bench.core import FunctionBlock
from trpc_agent_bench.core import TextBlock


def _event():
    part = SimpleNamespace(
        text="ok",
        thought=False,
        function_call=None,
        function_response=None,
    )
    event = SimpleNamespace(
        content=SimpleNamespace(parts=[part]),
        partial=False,
        turn_complete=True,
    )
    event.is_error = lambda: False
    return event


def test_concurrent_sessions_use_distinct_user_ids() -> None:

    class Handle(AgentHandle):

        def __init__(self) -> None:
            self.user_ids = []
            self.session_ids = []
            self.app_names = []

        async def run_turn(self, *, app_name, user_id, session_id, message):
            self.user_ids.append(user_id)
            self.session_ids.append(session_id)
            self.app_names.append(app_name)
            yield _event()

    handle = Handle()
    func = _make_conversation(
        handle=handle,
        app_name="bench_app",
        base_user_id="bench_user",
        script=[[TextBlock()]],
        current=3,
    )
    asyncio.run(func())

    assert sorted(handle.user_ids) == ["bench_user_0", "bench_user_1", "bench_user_2"]
    assert sorted(handle.app_names) == ["bench_app"] * 3
    assert len(set(handle.session_ids)) == 3


def test_session_user_id_appends_index() -> None:
    assert [_session_user_id("bench_user", index) for index in range(3)] == [
        "bench_user_0",
        "bench_user_1",
        "bench_user_2",
    ]


def test_turn_counts_are_fixed() -> None:
    assert turn1.TURN_COUNT == 1
    assert turn5.TURN_COUNT == 5
    assert turn10.TURN_COUNT == 10


def test_turn_scripts_are_single_chunk_text() -> None:
    for module in (turn1, turn5, turn10):
        assert len(module.TURN_SCRIPT) == module.TURN_COUNT
        for turn in module.TURN_SCRIPT:
            assert len(turn) == 1
            assert isinstance(turn[0], TextBlock)
            assert turn[0].chunks == 1


def test_loop_scripts_have_n_calls_and_n_minus_one_tool_rounds() -> None:
    for module, llm_calls in ((loop1, 1), (loop5, 5), (loop10, 10)):
        assert module.LLM_CALLS == llm_calls
        assert module.TOOL_ROUNDS == llm_calls - 1
        assert len(module.TURN_SCRIPT) == 1

        replies = module.TURN_SCRIPT[0]
        assert len(replies) == llm_calls
        for reply in replies[:-1]:
            assert isinstance(reply, list)
            assert all(isinstance(block, FunctionBlock) for block in reply)
        assert isinstance(replies[-1], TextBlock)
        assert replies[-1].chunks == 5


def test_loop_tool_calls_use_distinct_arguments() -> None:
    replies = make_loop_script(5)[0]
    args = [reply[0].args for reply in replies[:-1]]

    assert args == [
        {
            "a": 1,
            "b": 2
        },
        {
            "a": 2,
            "b": 3
        },
        {
            "a": 3,
            "b": 4
        },
        {
            "a": 4,
            "b": 5
        },
    ]


def test_script_builders_reject_empty_scripts() -> None:
    with pytest.raises(ValueError, match="user_turns"):
        make_turn_script(0)
    with pytest.raises(ValueError, match="llm_calls"):
        make_loop_script(0)


def test_expected_text_and_tool_detection() -> None:
    assert _expected_text([TextBlock("done")]) == "done"
    assert _expected_text([[FunctionBlock("add_numbers", {"a": 1, "b": 2})], TextBlock("ok")]) == "ok"
    assert _has_function([[FunctionBlock("add_numbers", {"a": 1, "b": 2})], TextBlock()]) is True
    assert _has_function([TextBlock()]) is False


def test_turn_and_loop_modules_declare_profiles() -> None:
    for module in (turn1, turn5, turn10, loop1, loop5, loop10):
        profile = module.get_bench_profile()
        assert profile.usable is True
        assert profile.supports_concurrency is True
        assert profile.requires_iterations is True


def test_fixed_sample_benchmarks_do_not_require_iterations() -> None:
    assert import_time.get_bench_profile().requires_iterations is False
    assert memory_footprint.get_bench_profile().requires_iterations is False
