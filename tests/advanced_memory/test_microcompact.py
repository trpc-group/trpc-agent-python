"""Unit tests for mechanically cleaning old tool results."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from trpc_agent_sdk.advanced_memory import AdvancedMemoryConfig
from trpc_agent_sdk.advanced_memory import AdvancedMemoryRuntime
from trpc_agent_sdk.advanced_memory import Microcompact
from trpc_agent_sdk.advanced_memory import MicrocompactCallback
from trpc_agent_sdk.advanced_memory import setup_microcompact
from trpc_agent_sdk.advanced_memory import setup_tool_result_budget
from trpc_agent_sdk.advanced_memory import ToolResultBudgetCallback
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionResponse
from trpc_agent_sdk.types import Part


def _runtime(
    tmp_path: Path,
    *,
    enabled: bool = True,
    microcompact_enabled: bool = True,
    trigger_count: int = 4,
    keep_recent: int = 2,
    gap_seconds: float = 60.0,
) -> AdvancedMemoryRuntime:
    """Create an isolated runtime with small mechanical-compaction limits."""
    return AdvancedMemoryRuntime.create(
        AdvancedMemoryConfig(
            enabled=enabled,
            root_dir=tmp_path,
            tool_result_max_chars=1_000,
            tool_result_preview_chars=100,
            microcompact_enabled=microcompact_enabled,
            microcompact_trigger_count=trigger_count,
            microcompact_keep_recent=keep_recent,
            microcompact_gap_seconds=gap_seconds,
        ))


def _request(count: int, *, tool_name: str = "Read") -> tuple[LlmRequest, list[Part]]:
    """Create a model request with a specified number of tool results."""
    parts = [
        Part(function_response=FunctionResponse(
            id=f"result-{index}",
            name=tool_name,
            response={"output": chr(97 + index) * 200},
        )) for index in range(count)
    ]
    return LlmRequest(model="test-model", contents=[Content(role="user", parts=parts)]), parts


def _outputs(request: LlmRequest) -> list[str]:
    """Extract the output text for each tool result."""
    return [part.function_response.response["output"] for part in request.contents[0].parts]


async def test_count_trigger_clears_old_results_and_keeps_recent(tmp_path: Path) -> None:
    """Ensure count pressure cleans only old results."""
    request, original_parts = _request(5)
    original_first_response = original_parts[0].function_response.response.copy()

    result = await Microcompact(_runtime(tmp_path)).apply(
        request,
        session_id="session-a",
        last_assistant_timestamp=None,
    )

    outputs = _outputs(request)
    assert result.trigger == "count"
    assert result.cleared_count == 3
    assert outputs[:3] == ["[Old tool result content cleared]"] * 3
    assert outputs[3:] == ["d" * 200, "e" * 200]
    assert original_parts[0].function_response.response == original_first_response


async def test_time_trigger_runs_below_count_threshold(tmp_path: Path) -> None:
    """Ensure a long time gap cleans old results before count pressure."""
    request, _ = _request(4)

    result = await Microcompact(_runtime(tmp_path)).apply(
        request,
        session_id="session-a",
        last_assistant_timestamp=100.0,
        now=161.0,
    )

    assert result.trigger == "time"
    assert result.cleared_count == 2
    assert _outputs(request)[:2] == ["[Old tool result content cleared]"] * 2


async def test_time_trigger_does_not_clear_when_only_recent_results_exist(tmp_path: Path) -> None:
    """Ensure the configured recent results remain after time pressure."""
    request, _ = _request(2)

    result = await Microcompact(_runtime(tmp_path)).apply(
        request,
        session_id="session-a",
        last_assistant_timestamp=100.0,
        now=161.0,
    )

    assert result.trigger is None
    assert result.cleared_count == 0
    assert _outputs(request) == ["a" * 200, "b" * 200]


async def test_cleared_results_are_reapplied_after_restart(tmp_path: Path) -> None:
    """Ensure restart restores and reapplies the same cleanup."""
    first_request, _ = _request(5)
    await Microcompact(_runtime(tmp_path)).apply(
        first_request,
        session_id="session-a",
        last_assistant_timestamp=None,
    )

    second_request, _ = _request(3)
    result = await Microcompact(_runtime(tmp_path)).apply(
        second_request,
        session_id="session-a",
        last_assistant_timestamp=None,
    )
    records = await _runtime(tmp_path).transcripts.read_all("session-a")

    assert result.trigger is None
    assert result.reapplied_count == 3
    assert _outputs(second_request) == ["[Old tool result content cleared]"] * 3
    assert len([record for record in records if record["kind"] == "microcompact-clear"]) == 3


async def test_non_compactable_tools_are_ignored(tmp_path: Path) -> None:
    """Ensure unconfigured tools do not affect thresholds or cleanup."""
    request, _ = _request(6, tool_name="CustomTool")

    result = await Microcompact(_runtime(tmp_path)).apply(
        request,
        session_id="session-a",
        last_assistant_timestamp=None,
    )

    assert result.cleared_count == 0
    assert _outputs(request)[0] == "a" * 200


async def test_disabled_microcompact_does_not_copy_or_persist(tmp_path: Path) -> None:
    """Ensure disabled compaction preserves requests and disk state."""
    request, _ = _request(5)
    original_content = request.contents[0]

    result = await Microcompact(_runtime(tmp_path, microcompact_enabled=False)).apply(
        request,
        session_id="session-a",
        last_assistant_timestamp=None,
    )

    assert result.cleared_count == 0
    assert request.contents[0] is original_content
    assert not (tmp_path / "SESSION").exists()


def test_setup_orders_budget_before_microcompact_in_both_call_orders(tmp_path: Path) -> None:
    """Ensure both setup functions keep budgeting before mechanical cleanup."""
    runtime = _runtime(tmp_path)
    first_agent = SimpleNamespace(before_model_callback=None)
    setup_microcompact(first_agent, runtime)
    setup_tool_result_budget(first_agent, runtime)

    second_agent = SimpleNamespace(before_model_callback=None)
    setup_tool_result_budget(second_agent, runtime)
    setup_microcompact(second_agent, runtime)

    for agent in (first_agent, second_agent):
        assert isinstance(agent.before_model_callback[0], ToolResultBudgetCallback)
        assert isinstance(agent.before_model_callback[1], MicrocompactCallback)
