"""Unit tests for history snip under context pressure."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from trpc_agent_sdk.advanced_memory import AdvancedMemoryConfig
from trpc_agent_sdk.advanced_memory import AdvancedMemoryRuntime
from trpc_agent_sdk.advanced_memory import HistorySnip
from trpc_agent_sdk.advanced_memory import HistorySnipCallback
from trpc_agent_sdk.advanced_memory import Microcompact
from trpc_agent_sdk.advanced_memory import MicrocompactCallback
from trpc_agent_sdk.advanced_memory import setup_history_snip
from trpc_agent_sdk.advanced_memory import setup_microcompact
from trpc_agent_sdk.advanced_memory import setup_tool_result_budget
from trpc_agent_sdk.advanced_memory import ToolResultBudgetCallback
from trpc_agent_sdk.advanced_memory import ToolResultBudget
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionResponse
from trpc_agent_sdk.types import Part


def _runtime(
    tmp_path: Path,
    *,
    enabled: bool = True,
    snip_enabled: bool = True,
    trigger_chars: int = 1_000,
    target_chars: int = 600,
    keep_recent: int = 2,
) -> AdvancedMemoryRuntime:
    """Create an isolated runtime with small history-snip limits."""
    return AdvancedMemoryRuntime.create(
        AdvancedMemoryConfig(
            enabled=enabled,
            root_dir=tmp_path,
            tool_result_max_chars=5_000,
            tool_result_preview_chars=100,
            history_snip_enabled=snip_enabled,
            history_snip_trigger_chars=trigger_chars,
            history_snip_target_chars=target_chars,
            history_snip_keep_recent=keep_recent,
        ))


def _request(count: int, *, output_size: int = 400) -> tuple[LlmRequest, list[Part]]:
    """Create a model request with sized tool results."""
    parts = [
        Part(function_response=FunctionResponse(
            id=f"result-{index}",
            name="Read",
            response={"output": chr(97 + index) * output_size},
        )) for index in range(count)
    ]
    return LlmRequest(model="test-model", contents=[Content(role="user", parts=parts)]), parts


def _outputs(request: LlmRequest) -> list[str]:
    """Extract all tool outputs from a model request."""
    return [part.function_response.response["output"] for part in request.contents[0].parts]


async def test_pressure_snips_old_results_and_keeps_recent(tmp_path: Path) -> None:
    """Ensure oversized requests clean old results and keep recent work."""
    request, original_parts = _request(4)
    original_response = original_parts[0].function_response.response.copy()

    result = await HistorySnip(_runtime(tmp_path)).apply(
        request,
        session_id="session-a",
    )

    outputs = _outputs(request)
    assert result.trigger == "pressure"
    assert result.snipped_count == 2
    assert result.request_chars_after < result.request_chars_before
    assert outputs[:2] == ["[Older tool result removed by history snip]"] * 2
    assert outputs[2:] == ["c" * 400, "d" * 400]
    assert original_parts[0].function_response.response == original_response


async def test_token_budget_triggers_snip_without_character_pressure(tmp_path: Path) -> None:
    """Ensure a configured model window triggers cleanup by token warning."""
    request, _ = _request(4, output_size=1_000)
    runtime = AdvancedMemoryRuntime.create(
        AdvancedMemoryConfig(
            enabled=True,
            root_dir=tmp_path,
            tool_result_max_chars=10_000,
            tool_result_preview_chars=100,
            history_snip_trigger_chars=100_000,
            history_snip_target_chars=50_000,
            history_snip_keep_recent=2,
            model_context_window_tokens=1_000,
            max_output_tokens=100,
        ))

    result = await HistorySnip(runtime).apply(request, session_id="session-a")

    assert result.trigger == "pressure"
    assert result.snipped_count == 2
    assert result.request_tokens_before is not None
    assert result.request_tokens_after is not None
    assert result.request_tokens_after < result.request_tokens_before


async def test_request_below_trigger_remains_unchanged(tmp_path: Path) -> None:
    """Ensure cleanup does not run below the configured threshold."""
    request, _ = _request(2, output_size=50)

    result = await HistorySnip(_runtime(tmp_path)).apply(
        request,
        session_id="session-a",
    )

    assert result.trigger is None
    assert result.snipped_count == 0
    assert _outputs(request) == ["a" * 50, "b" * 50]


async def test_force_snip_runs_below_pressure_threshold(tmp_path: Path) -> None:
    """Ensure force mode cleans results before the recent working set."""
    request, _ = _request(4, output_size=100)

    result = await HistorySnip(_runtime(tmp_path, trigger_chars=10_000, target_chars=5_000)).apply(
        request,
        session_id="session-a",
        force=True,
    )

    assert result.trigger == "force"
    assert result.snipped_count == 2
    assert _outputs(request)[:2] == ["[Older tool result removed by history snip]"] * 2


async def test_snipped_results_are_reapplied_after_restart(tmp_path: Path) -> None:
    """Ensure history-snip decisions can be restored from the transcript."""
    first_request, _ = _request(4)
    await HistorySnip(_runtime(tmp_path)).apply(
        first_request,
        session_id="session-a",
    )

    second_request, _ = _request(2)
    result = await HistorySnip(_runtime(tmp_path)).apply(
        second_request,
        session_id="session-a",
    )
    records = await _runtime(tmp_path).transcripts.read_all("session-a")

    assert result.trigger is None
    assert result.reapplied_count == 2
    assert _outputs(second_request) == ["[Older tool result removed by history snip]"] * 2
    assert len([record for record in records if record["kind"] == "history-snip"]) == 2


async def test_budget_recovery_pointer_survives_later_shrink_stages(tmp_path: Path, ) -> None:
    """Ensure snip and Microcompact preserve budget-generated result paths."""
    runtime = AdvancedMemoryRuntime.create(
        AdvancedMemoryConfig(
            enabled=True,
            root_dir=tmp_path,
            tool_result_max_chars=200,
            tool_results_per_message_max_chars=10_000,
            tool_result_preview_chars=40,
            history_snip_trigger_chars=1_000,
            history_snip_target_chars=500,
            history_snip_keep_recent=1,
            microcompact_trigger_count=2,
            microcompact_keep_recent=1,
        ))
    request, _ = _request(5, output_size=100)
    request.contents[0].parts[0].function_response.response = {"output": "oversized" * 100}

    await ToolResultBudget(runtime).apply(request, session_id="session-a")
    recovery_response = request.contents[0].parts[0].function_response.response
    recovery_path = recovery_response["persisted_output"]["path"]
    await HistorySnip(runtime).apply(
        request,
        session_id="session-a",
        force=True,
    )
    await Microcompact(runtime).apply(
        request,
        session_id="session-a",
        last_assistant_timestamp=None,
    )

    final_response = request.contents[0].parts[0].function_response.response
    assert final_response["persisted_output"]["path"] == recovery_path
    records = await runtime.transcripts.read_all("session-a")
    assert not any(
        record.get("result_id") == "result-0" and record.get("kind") in {"history-snip", "microcompact-clear"}
        for record in records)


async def test_disabled_history_snip_does_not_copy_or_persist(tmp_path: Path) -> None:
    """Ensure disabled history snip does not copy requests or create storage."""
    request, _ = _request(4)
    original_content = request.contents[0]

    result = await HistorySnip(_runtime(tmp_path, snip_enabled=False)).apply(
        request,
        session_id="session-a",
    )

    assert result.snipped_count == 0
    assert request.contents[0] is original_content
    assert not (tmp_path / "SESSION").exists()


def test_setup_orders_all_context_callbacks_by_stage(tmp_path: Path) -> None:
    """Ensure any installation order yields the fixed callback order."""
    runtime = _runtime(tmp_path)
    agent = SimpleNamespace(before_model_callback=None)

    setup_microcompact(agent, runtime)
    setup_history_snip(agent, runtime)
    setup_tool_result_budget(agent, runtime)

    assert isinstance(agent.before_model_callback[0], ToolResultBudgetCallback)
    assert isinstance(agent.before_model_callback[1], HistorySnipCallback)
    assert isinstance(agent.before_model_callback[2], MicrocompactCallback)
