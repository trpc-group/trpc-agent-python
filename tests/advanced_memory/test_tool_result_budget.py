"""Unit tests for tool-result context budgeting."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from trpc_agent_sdk.advanced_memory import AdvancedMemoryConfig
from trpc_agent_sdk.advanced_memory import AdvancedMemoryRuntime
from trpc_agent_sdk.advanced_memory import setup_tool_result_budget
from trpc_agent_sdk.advanced_memory import ToolResultBudget
from trpc_agent_sdk.advanced_memory import ToolResultBudgetCallback
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionResponse
from trpc_agent_sdk.types import Part


def _runtime(
    tmp_path: Path,
    *,
    enabled: bool = True,
    per_result: int = 500,
    per_message: int = 2_000,
    preview: int = 50,
) -> AdvancedMemoryRuntime:
    """Create an isolated runtime with small test limits."""
    return AdvancedMemoryRuntime.create(
        AdvancedMemoryConfig(
            enabled=enabled,
            root_dir=tmp_path,
            tool_result_max_chars=per_result,
            tool_results_per_message_max_chars=per_message,
            tool_result_preview_chars=preview,
        ))


def _request(*responses: tuple[str, str]) -> tuple[LlmRequest, list[Part]]:
    """Create one user Content request from a tool ID and output text."""
    parts = [
        Part(function_response=FunctionResponse(
            id=result_id,
            name="demo_tool",
            response={"output": output},
        )) for result_id, output in responses
    ]
    return LlmRequest(model="test-model", contents=[Content(role="user", parts=parts)]), parts


async def test_single_large_result_is_persisted_and_replaced(tmp_path: Path) -> None:
    """Ensure oversized single results are persisted and previewed."""
    runtime = _runtime(tmp_path, per_result=200, per_message=5_000, preview=40)
    budget = ToolResultBudget(runtime)
    request, original_parts = _request(("result-1", "x" * 500))
    original_response = original_parts[0].function_response.response.copy()

    result = await budget.apply(request, session_id="session-a")

    replacement = request.contents[0].parts[0].function_response.response
    assert result.replaced_count == 1
    assert "persisted_output" in replacement
    assert replacement["persisted_output"]["truncated"] is True
    assert original_parts[0].function_response.response == original_response
    persisted = await runtime.tool_results.read("session-a", "result-1")
    assert persisted is not None
    assert '"output":"' in persisted
    assert "x" * 100 in persisted


async def test_aggregate_budget_replaces_largest_fresh_results(tmp_path: Path) -> None:
    """Ensure aggregate pressure replaces the largest new result first."""
    runtime = _runtime(tmp_path, per_result=5_000, per_message=2_300, preview=50)
    budget = ToolResultBudget(runtime)
    request, _ = _request(
        ("small", "s" * 400),
        ("largest", "l" * 1_400),
        ("medium", "m" * 900),
    )

    result = await budget.apply(request, session_id="session-a")

    responses = {part.function_response.id: part.function_response.response for part in request.contents[0].parts}
    assert result.replaced_count == 1
    assert "persisted_output" in responses["largest"]
    assert responses["small"]["output"] == "s" * 400
    assert responses["medium"]["output"] == "m" * 900


async def test_aggregate_budget_groups_consecutive_user_contents(tmp_path: Path, ) -> None:
    """Ensure consecutive user Contents share one aggregate budget."""
    runtime = _runtime(tmp_path, per_result=5_000, per_message=1_800, preview=50)
    request = LlmRequest(
        model="test-model",
        contents=[
            Content(
                role="user",
                parts=[
                    Part(function_response=FunctionResponse(
                        id=f"result-{index}",
                        name="demo_tool",
                        response={"output": char * 1_100},
                    ))
                ],
            ) for index, char in enumerate(("a", "b"))
        ],
    )

    result = await ToolResultBudget(runtime).apply(
        request,
        session_id="session-a",
    )

    responses = [content.parts[0].function_response.response for content in request.contents]
    assert result.replaced_count == 1
    assert sum("persisted_output" in response for response in responses) == 1


async def test_model_content_starts_a_new_aggregate_budget_group(tmp_path: Path, ) -> None:
    """Ensure results after a model boundary are not merged with the prior group."""
    runtime = _runtime(tmp_path, per_result=5_000, per_message=1_800, preview=50)
    request = LlmRequest(
        model="test-model",
        contents=[
            Content(
                role="user",
                parts=[
                    Part(function_response=FunctionResponse(
                        id="result-1",
                        name="demo_tool",
                        response={"output": "a" * 1_100},
                    ))
                ],
            ),
            Content(
                role="model",
                parts=[Part.from_text(text="继续调用工具")],
            ),
            Content(
                role="user",
                parts=[
                    Part(function_response=FunctionResponse(
                        id="result-2",
                        name="demo_tool",
                        response={"output": "b" * 1_100},
                    ))
                ],
            ),
        ],
    )

    result = await ToolResultBudget(runtime).apply(
        request,
        session_id="session-a",
    )

    assert result.replaced_count == 0


async def test_reapplying_budget_uses_exact_cached_replacement(tmp_path: Path) -> None:
    """Ensure repeated requests reuse replacements without duplicate records."""
    runtime = _runtime(tmp_path, per_result=200, per_message=5_000, preview=40)
    budget = ToolResultBudget(runtime)
    first_request, _ = _request(("result-1", "x" * 500))
    await budget.apply(first_request, session_id="session-a")
    first_replacement = first_request.contents[0].parts[0].function_response.response

    second_request, _ = _request(("result-1", "x" * 500))
    second_result = await budget.apply(second_request, session_id="session-a")
    second_replacement = second_request.contents[0].parts[0].function_response.response

    records = await runtime.transcripts.read_all("session-a")
    replacement_records = [record for record in records if record["kind"] == "content-replacement"]
    assert second_result.replaced_count == 0
    assert second_replacement == first_replacement
    assert len(replacement_records) == 1


async def test_unreplaced_result_remains_frozen_after_restart(tmp_path: Path) -> None:
    """Ensure already-sent results do not change after restart or lower limits."""
    first_runtime = _runtime(tmp_path, per_result=2_000, per_message=5_000, preview=40)
    first_budget = ToolResultBudget(first_runtime)
    first_request, _ = _request(("result-1", "x" * 500))
    await first_budget.apply(first_request, session_id="session-a")

    second_runtime = _runtime(tmp_path, per_result=200, per_message=1_000, preview=40)
    second_budget = ToolResultBudget(second_runtime)
    second_request, _ = _request(("result-1", "x" * 500))
    second_result = await second_budget.apply(second_request, session_id="session-a")

    response = second_request.contents[0].parts[0].function_response.response
    assert second_result.replaced_count == 0
    assert response["output"] == "x" * 500
    assert await second_runtime.tool_results.read("session-a", "result-1") is None


async def test_disabled_budget_does_not_copy_or_persist_request(tmp_path: Path) -> None:
    """Ensure disabled mode preserves the request and disk state."""
    runtime = _runtime(tmp_path, enabled=False)
    budget = ToolResultBudget(runtime)
    request, original_parts = _request(("result-1", "x" * 1_000))
    original_content = request.contents[0]

    result = await budget.apply(request, session_id="session-a")

    assert result.replaced_count == 0
    assert request.contents[0] is original_content
    assert request.contents[0].parts[0] is original_parts[0]
    assert not (tmp_path / "MEMORY").exists()
    assert not (tmp_path / "SESSION").exists()


async def test_exact_single_result_limit_is_not_replaced(tmp_path: Path) -> None:
    """Ensure a result exactly at the per-item limit is not replaced."""
    probe_request, _ = _request(("result-1", "x" * 100))
    probe_response = probe_request.contents[0].parts[0].function_response.response
    serialized_size = len(json.dumps(
        probe_response,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ))
    runtime = _runtime(
        tmp_path,
        per_result=serialized_size,
        per_message=5_000,
        preview=20,
    )
    request, _ = _request(("result-1", "x" * 100))

    result = await ToolResultBudget(runtime).apply(request, session_id="session-a")

    assert result.replaced_count == 0
    assert request.contents[0].parts[0].function_response.response["output"] == "x" * 100


async def test_aggregate_budget_is_independent_across_model_boundaries(tmp_path: Path, ) -> None:
    """Ensure model-separated result groups budget independently."""
    runtime = _runtime(tmp_path, per_result=5_000, per_message=1_500, preview=40)
    first_request, _ = _request(("first", "a" * 1_000))
    second_request, _ = _request(("second", "b" * 1_000))
    request = LlmRequest(
        model="test-model",
        contents=[
            first_request.contents[0],
            Content(role="model", parts=[Part.from_text(text="next")]),
            second_request.contents[0],
        ],
    )

    result = await ToolResultBudget(runtime).apply(request, session_id="session-a")

    assert result.replaced_count == 0
    assert request.contents[0].parts[0].function_response.response["output"] == "a" * 1_000
    assert request.contents[2].parts[0].function_response.response["output"] == "b" * 1_000


def test_setup_preserves_existing_callback_and_is_idempotent(tmp_path: Path) -> None:
    """Ensure setup preserves callbacks and is idempotent."""

    async def existing_callback(ctx, request):
        """Simulate an existing model pre-callback."""
        return None

    agent = SimpleNamespace(before_model_callback=existing_callback)
    runtime = _runtime(tmp_path)

    first_budget = setup_tool_result_budget(agent, runtime)
    second_budget = setup_tool_result_budget(agent, runtime)

    assert first_budget is second_budget
    assert agent.before_model_callback[0] is existing_callback
    assert isinstance(agent.before_model_callback[1], ToolResultBudgetCallback)
    assert len(agent.before_model_callback) == 2


async def test_reused_result_id_with_different_content_is_rejected(tmp_path: Path) -> None:
    """Ensure conflicting duplicate tool IDs fail instead of reusing replacements."""
    runtime = _runtime(tmp_path)
    request, _ = _request(
        ("duplicate", "first"),
        ("duplicate", "second"),
    )

    with pytest.raises(ValueError, match="reused with different content"):
        await ToolResultBudget(runtime).apply(request, session_id="session-a")


async def test_reused_result_id_after_restart_is_rejected(tmp_path: Path) -> None:
    """Ensure transcript state rejects tool-ID conflicts after restart."""
    first_runtime = _runtime(tmp_path, per_result=200, per_message=5_000, preview=40)
    first_request, _ = _request(("result-1", "first" * 100))
    await ToolResultBudget(first_runtime).apply(first_request, session_id="session-a")

    second_runtime = _runtime(tmp_path, per_result=200, per_message=5_000, preview=40)
    second_request, _ = _request(("result-1", "second" * 100))

    with pytest.raises(ValueError, match="reused with different content"):
        await ToolResultBudget(second_runtime).apply(second_request, session_id="session-a")


def test_setup_rejects_another_runtime_for_same_agent(tmp_path: Path) -> None:
    """Ensure one Agent cannot silently bind two budget runtimes."""
    agent = SimpleNamespace(before_model_callback=None)
    setup_tool_result_budget(agent, _runtime(tmp_path / "first"))

    with pytest.raises(ValueError, match="another runtime"):
        setup_tool_result_budget(agent, _runtime(tmp_path / "second"))
