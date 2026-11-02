"""Unit tests for stats, assertions, and behaviors."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from trpc_agent_bench.bench._eval import ensure_turn_completed
from trpc_agent_bench.bench._eval import get_iterations
from trpc_agent_bench.bench._eval import percentile
from trpc_agent_bench.bench._eval import write_result
from trpc_agent_bench.bench._model import FunctionBlock
from trpc_agent_bench.bench._model import TextBlock
from trpc_agent_bench.bench._model import _split_text
from trpc_agent_bench.bench._model import reply_from_script
from trpc_agent_bench.bench._model import request_position
from trpc_agent_bench.bench._model import script_spec
from trpc_agent_bench.bench._model import turns_from_spec
from trpc_agent_bench.bench._setup import get_concurrency


def _part(text=None, function_call=None, function_response=None):
    return SimpleNamespace(
        text=text,
        thought=False,
        function_call=function_call,
        function_response=function_response,
    )


def _event(parts, *, partial=False, turn_complete=False, error=False):
    event = SimpleNamespace(
        content=SimpleNamespace(parts=parts),
        partial=partial,
        turn_complete=turn_complete,
    )
    event.is_error = lambda: error
    return event


def test_percentile_inclusive() -> None:
    assert percentile([], 50) == 0.0
    assert percentile([1.0], 95) == 1.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 50) == 2.5
    assert percentile([1.0, 2.0, 3.0, 4.0], 95) == pytest.approx(3.85)


def test_get_iterations_reads_and_validates_env(monkeypatch: pytest.MonkeyPatch, ) -> None:
    monkeypatch.delenv("BENCH_ITERATIONS", raising=False)
    with pytest.raises(RuntimeError, match="BENCH_ITERATIONS is not set"):
        get_iterations()

    monkeypatch.setenv("BENCH_ITERATIONS", "5")
    assert get_iterations() == 5

    monkeypatch.setenv("BENCH_ITERATIONS", "0")
    with pytest.raises(ValueError, match="positive"):
        get_iterations()

    monkeypatch.setenv("BENCH_ITERATIONS", "many")
    with pytest.raises(ValueError, match="integer"):
        get_iterations()


def test_get_concurrency_defaults_and_validates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BENCH_CONCURRENCY", raising=False)
    assert get_concurrency() == 1

    monkeypatch.setenv("BENCH_CONCURRENCY", "8")
    assert get_concurrency() == 8

    monkeypatch.setenv("BENCH_CONCURRENCY", "0")
    with pytest.raises(ValueError, match="positive"):
        get_concurrency()

    monkeypatch.setenv("BENCH_CONCURRENCY", "many")
    with pytest.raises(ValueError, match="integer"):
        get_concurrency()


def test_turn_completed_accepts_partial_only_text() -> None:
    events = [_event([_part(text="ok")], partial=True)]
    ensure_turn_completed(events, expected_text="ok")


def test_turn_completed_rejects_error() -> None:
    events = [_event([_part(text="ok")], partial=True, error=True)]
    with pytest.raises(RuntimeError, match="error event"):
        ensure_turn_completed(events, expected_text="ok")


def test_turn_completed_rejects_missing_text() -> None:
    events = [_event([])]
    with pytest.raises(RuntimeError, match="did not complete"):
        ensure_turn_completed(events)


def test_turn_completed_ignores_surrounding_whitespace() -> None:
    events = [_event([_part(text="\n\ndone\n")], partial=True)]
    ensure_turn_completed(events, expected_text="done")


def test_turn_completed_rejects_wrong_text() -> None:
    events = [_event([_part(text="nope")], partial=True)]
    with pytest.raises(RuntimeError, match="unexpected final text"):
        ensure_turn_completed(events, expected_text="ok")


def test_tool_success_requires_call_and_response() -> None:
    call = SimpleNamespace(name="add_numbers")
    response = SimpleNamespace(name="add_numbers", response={"result": 3})
    events = [
        _event([_part(function_call=call)], partial=True),
        _event([_part(function_response=response)]),
    ]
    ensure_turn_completed(events, expect_tool_success=True)


def test_text_block_chunks() -> None:
    reply = reply_from_script(TextBlock("ok", chunks=3), turn=1, step=0)
    assert reply.text == "ok"
    assert reply.chunks == 3


def test_function_block_reply_has_tool_call() -> None:
    reply = reply_from_script(
        [FunctionBlock("add_numbers", {
            "a": 1,
            "b": 2
        })],
        turn=1,
        step=0,
    )
    assert reply.tool_calls[0]["name"] == "add_numbers"
    assert reply.tool_calls[0]["args"] == {"a": 1, "b": 2}
    assert reply.finish_reason == "tool_calls"


def test_parallel_function_blocks_have_distinct_arguments() -> None:
    reply = reply_from_script(
        [
            FunctionBlock("add_numbers", {
                "a": 1,
                "b": 2
            }),
            FunctionBlock("add_numbers", {
                "a": 2,
                "b": 3
            }),
            FunctionBlock("add_numbers", {
                "a": 3,
                "b": 4
            }),
        ],
        turn=1,
        step=0,
    )
    assert len(reply.tool_calls) == 3
    arguments = [call["args"] for call in reply.tool_calls]
    assert len({(item["a"], item["b"]) for item in arguments}) == 3


def test_split_text_returns_exact_chunk_count() -> None:
    assert _split_text("ok", 10) == ["o", "k", "", "", "", "", "", "", "", ""]
    assert _split_text("abcdef", 3) == ["ab", "cd", "ef"]


def test_script_spec_round_trip() -> None:
    turns = [
        [TextBlock("ok", chunks=2)],
        [
            [FunctionBlock("add_numbers", {
                "a": 1,
                "b": 2
            })],
            TextBlock("done"),
        ],
    ]
    assert turns_from_spec(script_spec(turns)) == turns


def test_request_position_counts_turns_and_replies() -> None:
    messages = [
        {
            "role": "user",
            "content": "first"
        },
        {
            "role": "assistant",
            "content": "ok"
        },
        {
            "role": "user",
            "content": "second"
        },
        {
            "role": "assistant",
            "content": "tool call"
        },
        {
            "role": "tool",
            "content": "result"
        },
    ]
    assert request_position(messages) == (1, 1)
    assert request_position([{"role": "user", "content": "only"}]) == (0, 0)


def test_write_result_uses_description_env_and_explicit_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BENCH_QUIET", "1")
    monkeypatch.setenv("BENCH_DESCRIPTION", "env description")

    payload = write_result(
        name="from_env",
        result={"median_run_time": 0.1},
        results_dir=tmp_path,
    )
    assert payload["description"] == "env description"
    assert json.loads((tmp_path / "from_env.json").read_text(encoding="utf-8"))["description"] == "env description"

    payload = write_result(
        name="explicit",
        result={"median_run_time": 0.1},
        description="explicit description",
        results_dir=tmp_path,
    )
    assert payload["description"] == "explicit description"


def test_write_result_keeps_display_name_and_label(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BENCH_QUIET", "1")

    payload = write_result(
        name="turn1_redis_c8",
        display_name="turn1_redis",
        label="concurrency=8",
        result={"median_run_time": 0.1},
        results_dir=tmp_path,
    )

    assert payload["display_name"] == "turn1_redis"
    assert payload["label"] == "concurrency=8"
    stored = json.loads((tmp_path / "turn1_redis_c8.json").read_text(encoding="utf-8"))
    assert stored["display_name"] == "turn1_redis"
    assert stored["label"] == "concurrency=8"
