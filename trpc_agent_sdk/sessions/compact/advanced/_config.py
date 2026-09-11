# Tencent is pleased to support the open source ecosystem.
#
# Copyright (C) 2026 Tencent. All rights reserved.
# Licensed under Apache-2.0.
"""Configuration for Session Compact."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field

from ._base import BaseTokenEstimator
from ._base import BaseModelContextWindowResolver

DEFAULT_COMPACTABLE_TOOL_NAMES = (
    "Read",
    "Bash",
    "Grep",
    "Glob",
    "Search",
    "CodeSearch",
)


def _require_positive(**values: int | float) -> None:
    for name, value in values.items():
        if value <= 0:
            raise ValueError(f"{name} must be greater than zero")


def _require_non_negative(**values: int | float) -> None:
    for name, value in values.items():
        if value < 0:
            raise ValueError(f"{name} must be non-negative")


def _require_non_empty_names(name: str, values: tuple[str, ...]) -> None:
    if not values or any(not value.strip() for value in values):
        raise ValueError(f"{name} must contain non-empty names")


@dataclass(frozen=True)
class AutoCompactSummarizerConfig:
    """Configure auto compact summarizer."""
    enabled: bool = field(default=True)
    trigger_chars: int = field(default=700_000)
    target_chars: int = field(default=350_000)
    blocking_chars: int = field(default=780_000)
    keep_recent_contents: int = field(default=8)
    max_failures: int = field(default=3)
    summary_input_max_chars: int = field(default=600_000)
    summary_retries_count: int = field(default=3)


@dataclass(frozen=True)
class HistorySnipConfig:
    """Configure history snip."""
    enabled: bool = field(default=True)
    trigger_chars: int = field(default=600_000)
    target_chars: int = field(default=400_000)
    keep_recent: int = field(default=5)
    tool_names: tuple[str, ...] = field(default=DEFAULT_COMPACTABLE_TOOL_NAMES)


@dataclass(frozen=True)
class TokenContextTrackerConfig:
    """Configure token context tracker."""
    enabled: bool = field(default=True)
    warning_ratio: float = field(default=0.85)
    auto_compact_ratio: float = field(default=0.90)
    blocking_ratio: float = field(default=0.95)
    model_context_window_tokens: int | None = field(default=None)
    max_output_tokens: int = field(default=0)
    estimator: BaseTokenEstimator | None = field(default=None)
    context_window_resolver: BaseModelContextWindowResolver | None = field(default=None)


@dataclass(frozen=True)
class MicroCompactConfig:
    """Configure micro compact."""
    enabled: bool = field(default=True)
    gap_seconds: float = field(default=3_600.0)
    trigger_count: int = field(default=20)
    keep_recent: int = field(default=5)
    tool_names: tuple[str, ...] = field(default=DEFAULT_COMPACTABLE_TOOL_NAMES)


@dataclass(frozen=True)
class ToolResultBudgetConfig:
    """Configure tool result budget."""
    enabled: bool = field(default=True)
    max_chars: int = field(default=50_000)
    per_message_max_chars: int = field(default=200_000)
    preview_chars: int = field(default=2_000)


@dataclass(frozen=True)
class SessionMemoryExtractorConfig:
    """Configure session memory."""
    enabled: bool = field(default=True)
    initial_chars: int = field(default=40_000)
    update_chars: int = field(default=20_000)
    initial_tokens: int = field(default=10_000)
    update_tokens: int = field(default=5_000)
    tool_calls_between_updates: int = field(default=3)
    prompt_max_chars: int = field(default=200_000)
    request_overhead_tokens: int = field(default=2_048)
    section_max_chars: int = field(default=8_000)
    total_max_chars: int = field(default=54_000)
    wait_timeout_seconds: float = field(default=15.0)
    max_retries: int = field(default=1)


@dataclass(frozen=True)
class AdvancedAutoCompactSummarizerConfig:
    """Configure advanced compact."""
    history_snip: HistorySnipConfig = field(default_factory=HistorySnipConfig)
    token_context_tracker: TokenContextTrackerConfig = field(default_factory=TokenContextTrackerConfig)
    session_memory: SessionMemoryExtractorConfig = field(default_factory=SessionMemoryExtractorConfig)
    tool_result_budget: ToolResultBudgetConfig = field(default_factory=ToolResultBudgetConfig)
    micro_compact: MicroCompactConfig = field(default_factory=MicroCompactConfig)
    auto_compact: AutoCompactSummarizerConfig = field(default_factory=AutoCompactSummarizerConfig)

    def __post_init__(self) -> None:
        """Validate compression limits and token thresholds."""
        _require_positive(
            tool_result_budget_max_chars=self.tool_result_budget.max_chars,
            tool_result_budget_per_message_max_chars=self.tool_result_budget.per_message_max_chars,
            tool_result_budget_preview_chars=self.tool_result_budget.preview_chars,
            history_snip_trigger_chars=self.history_snip.trigger_chars,
            history_snip_target_chars=self.history_snip.target_chars,
            history_snip_keep_recent=self.history_snip.keep_recent,
            session_memory_initial_chars=self.session_memory.initial_chars,
            session_memory_update_chars=self.session_memory.update_chars,
            session_memory_initial_tokens=self.session_memory.initial_tokens,
            session_memory_update_tokens=self.session_memory.update_tokens,
            session_memory_tool_calls_between_updates=self.session_memory.tool_calls_between_updates,
            session_memory_prompt_max_chars=self.session_memory.prompt_max_chars,
            session_memory_section_max_chars=self.session_memory.section_max_chars,
            session_memory_total_max_chars=self.session_memory.total_max_chars,
            session_memory_wait_timeout_seconds=self.session_memory.wait_timeout_seconds,
            auto_compact_trigger_chars=self.auto_compact.trigger_chars,
            auto_compact_target_chars=self.auto_compact.target_chars,
            auto_compact_blocking_chars=self.auto_compact.blocking_chars,
            auto_compact_keep_recent_contents=self.auto_compact.keep_recent_contents,
            auto_compact_max_failures=self.auto_compact.max_failures,
            auto_compact_summary_input_max_chars=self.auto_compact.summary_input_max_chars,
            auto_compact_summary_retries_count=self.auto_compact.summary_retries_count,
            micro_compact_gap_seconds=self.micro_compact.gap_seconds,
            micro_compact_trigger_count=self.micro_compact.trigger_count,
            micro_compact_keep_recent=self.micro_compact.keep_recent,
        )
        _require_non_negative(
            max_output_tokens=self.token_context_tracker.max_output_tokens,
            session_memory_request_overhead_tokens=self.session_memory.request_overhead_tokens,
        )
        context_window_tokens = self.token_context_tracker.model_context_window_tokens
        if context_window_tokens is not None:
            _require_positive(model_context_window_tokens=context_window_tokens)
            if self.token_context_tracker.max_output_tokens >= context_window_tokens:
                raise ValueError("max_output_tokens must be smaller than model_context_window_tokens")
        if not (0 < self.token_context_tracker.warning_ratio < self.token_context_tracker.auto_compact_ratio <
                self.token_context_tracker.blocking_ratio < 1):
            raise ValueError("token context tracker ratios must satisfy 0 < warning < auto compact < blocking < 1")
        if self.tool_result_budget.preview_chars >= self.tool_result_budget.max_chars:
            raise ValueError("tool_result_budget.preview_chars must be smaller than tool_result_budget.max_chars")
        if self.history_snip.target_chars >= self.history_snip.trigger_chars:
            raise ValueError("history_snip.target_chars must be smaller than history_snip.trigger_chars")
        if self.auto_compact.trigger_chars <= self.auto_compact.target_chars:
            raise ValueError("auto_compact.trigger_chars must be greater than auto_compact.target_chars")
        if self.auto_compact.blocking_chars <= self.auto_compact.trigger_chars:
            raise ValueError("auto_compact.blocking_chars must be greater than auto_compact.trigger_chars")
        _require_non_empty_names("history_snip.tool_names", self.history_snip.tool_names)
        _require_non_empty_names("micro_compact.tool_names", self.micro_compact.tool_names)
