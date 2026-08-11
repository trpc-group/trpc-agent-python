"""Unit tests for token budgets and usage baselines."""

from __future__ import annotations

from types import SimpleNamespace

from trpc_agent_sdk.advanced_memory import AdvancedMemoryConfig
from trpc_agent_sdk.advanced_memory import TokenContextTracker
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


def _request(*texts: str) -> LlmRequest:
    """Build the smallest request useful for token estimation."""
    return LlmRequest(
        model="test-model",
        contents=[
            Content(
                role="model" if index % 2 else "user",
                parts=[Part.from_text(text=text)],
            ) for index, text in enumerate(texts)
        ],
    )


def test_usage_baseline_adds_only_contents_after_matching_event(tmp_path) -> None:
    """Ensure only content after a matching usage event is estimated."""
    request = _request("old user input", "model answer", "new user input")
    event = SimpleNamespace(
        id="event-1",
        content=request.contents[1],
        usage_metadata=SimpleNamespace(total_token_count=120),
    )
    ctx = SimpleNamespace(
        session=SimpleNamespace(events=[event]),
        agent=SimpleNamespace(model="test-model"),
    )
    tracker = TokenContextTracker(
        AdvancedMemoryConfig(
            enabled=True,
            root_dir=tmp_path,
            model_context_window_tokens=1_000,
            max_output_tokens=100,
        ))

    estimate = tracker.estimate(request, ctx)

    assert estimate.source == "hybrid"
    assert estimate.usage_event_id == "event-1"
    assert estimate.tokens > 120


def test_usage_boundary_mismatch_falls_back_to_full_request_estimate(tmp_path) -> None:
    """Ensure a missing safe usage boundary falls back to full estimation."""
    request = _request("current request")
    event = SimpleNamespace(
        id="event-1",
        content=Content(role="model", parts=[Part.from_text(text="removed answer")]),
        usage_metadata=SimpleNamespace(total_token_count=999_999),
    )
    ctx = SimpleNamespace(
        session=SimpleNamespace(events=[event]),
        agent=SimpleNamespace(model="test-model"),
    )
    tracker = TokenContextTracker(AdvancedMemoryConfig(enabled=True, root_dir=tmp_path))

    estimate = tracker.estimate(request, ctx)

    assert estimate.source == "estimated"
    assert estimate.tokens < 999_999


def test_changed_recorded_system_or_tool_fingerprint_falls_back(tmp_path) -> None:
    """Ensure changed instructions or tools invalidate the old usage baseline."""
    request = _request("user input", "model answer")
    event = SimpleNamespace(
        id="event-1",
        content=request.contents[1],
        usage_metadata=SimpleNamespace(total_token_count=999_999),
        custom_metadata={"advanced_memory_request_context_fingerprint": "outdated"},
    )
    ctx = SimpleNamespace(
        session=SimpleNamespace(events=[event]),
        agent=SimpleNamespace(model="test-model"),
    )

    estimate = TokenContextTracker(AdvancedMemoryConfig(enabled=True, root_dir=tmp_path)).estimate(request, ctx)

    assert estimate.source == "estimated"
    assert estimate.tokens < 999_999


def test_budget_reserves_max_output_and_calculates_three_thresholds(tmp_path) -> None:
    """Ensure thresholds use the window after reserving max output."""
    tracker = TokenContextTracker(
        AdvancedMemoryConfig(
            enabled=True,
            root_dir=tmp_path,
            model_context_window_tokens=10_000,
            max_output_tokens=2_000,
        ))

    budget = tracker.budget(_request("测试请求"))

    assert budget.effective_window_tokens == 8_000
    assert budget.warning_threshold_tokens == 6_800
    assert budget.autocompact_threshold_tokens == 7_200
    assert budget.blocking_threshold_tokens == 7_600


def test_no_window_keeps_compatibility_mode(tmp_path) -> None:
    """Ensure token decisions remain disabled without a model window."""
    budget = TokenContextTracker(AdvancedMemoryConfig(enabled=True,
                                                      root_dir=tmp_path)).budget(_request("compatibility request"))

    assert not budget.token_mode_enabled
    assert budget.estimate.source == "estimated"
