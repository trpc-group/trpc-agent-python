# Tencent is pleased to support the open source ecosystem.
#
# Copyright (C) 2026 Tencent. All rights reserved.
# Licensed under Apache-2.0.
"""Configuration for Session Compact."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Any

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
class AdvancedCompactConfig:
    """Configure compression that is persisted by the SessionService."""

    enabled: bool = True
    tool_result_max_chars: int = 50_000
    tool_results_per_message_max_chars: int = 200_000
    tool_result_preview_chars: int = 2_000
    history_snip_enabled: bool = True
    history_snip_trigger_chars: int = 600_000
    history_snip_target_chars: int = 400_000
    history_snip_keep_recent: int = 5
    history_snip_tool_names: tuple[str, ...] = DEFAULT_COMPACTABLE_TOOL_NAMES
    model_context_window_tokens: int | None = field(default=None)
    max_output_tokens: int = 0
    token_warning_ratio: float = 0.85
    token_autocompact_ratio: float = 0.90
    token_blocking_ratio: float = 0.95
    token_estimator: Any | None = field(default=None, repr=False, compare=False)
    context_window_resolver: Any | None = field(default=None, repr=False, compare=False)
    session_memory_enabled: bool = True
    session_memory_initial_chars: int = 40_000
    session_memory_update_chars: int = 20_000
    session_memory_initial_tokens: int = 10_000
    session_memory_update_tokens: int = 5_000
    session_memory_tool_calls_between_updates: int = 3
    session_memory_prompt_max_chars: int = 200_000
    session_memory_request_overhead_tokens: int = 2_048
    session_memory_section_max_chars: int = 8_000
    session_memory_total_max_chars: int = 54_000
    session_memory_wait_timeout_seconds: float = 15.0
    autocompact_enabled: bool = True
    autocompact_trigger_chars: int = 700_000
    autocompact_target_chars: int = 350_000
    autocompact_blocking_chars: int = 780_000
    autocompact_keep_recent_contents: int = 8
    autocompact_max_failures: int = 3
    autocompact_summary_input_max_chars: int = 600_000
    autocompact_summary_retries: int = 3
    microcompact_enabled: bool = True
    microcompact_gap_seconds: float = 3_600.0
    microcompact_trigger_count: int = 20
    microcompact_keep_recent: int = 5
    microcompact_tool_names: tuple[str, ...] = DEFAULT_COMPACTABLE_TOOL_NAMES

    def __post_init__(self) -> None:
        """Validate compression limits and token thresholds."""
        _require_positive(
            tool_result_max_chars=self.tool_result_max_chars,
            tool_results_per_message_max_chars=self.tool_results_per_message_max_chars,
            tool_result_preview_chars=self.tool_result_preview_chars,
            history_snip_trigger_chars=self.history_snip_trigger_chars,
            history_snip_target_chars=self.history_snip_target_chars,
            history_snip_keep_recent=self.history_snip_keep_recent,
            session_memory_initial_chars=self.session_memory_initial_chars,
            session_memory_update_chars=self.session_memory_update_chars,
            session_memory_initial_tokens=self.session_memory_initial_tokens,
            session_memory_update_tokens=self.session_memory_update_tokens,
            session_memory_tool_calls_between_updates=self.session_memory_tool_calls_between_updates,
            session_memory_prompt_max_chars=self.session_memory_prompt_max_chars,
            session_memory_section_max_chars=self.session_memory_section_max_chars,
            session_memory_total_max_chars=self.session_memory_total_max_chars,
            session_memory_wait_timeout_seconds=self.session_memory_wait_timeout_seconds,
            autocompact_target_chars=self.autocompact_target_chars,
            autocompact_max_failures=self.autocompact_max_failures,
            autocompact_summary_input_max_chars=self.autocompact_summary_input_max_chars,
            autocompact_summary_retries=self.autocompact_summary_retries,
            microcompact_gap_seconds=self.microcompact_gap_seconds,
            microcompact_trigger_count=self.microcompact_trigger_count,
            microcompact_keep_recent=self.microcompact_keep_recent,
        )
        _require_non_negative(
            max_output_tokens=self.max_output_tokens,
            session_memory_request_overhead_tokens=self.session_memory_request_overhead_tokens,
        )
        if self.model_context_window_tokens is not None:
            _require_positive(model_context_window_tokens=self.model_context_window_tokens)
            if self.max_output_tokens >= self.model_context_window_tokens:
                raise ValueError("max_output_tokens must be smaller than model_context_window_tokens")
        if not (0 < self.token_warning_ratio < self.token_autocompact_ratio < self.token_blocking_ratio < 1):
            raise ValueError("token ratios must satisfy 0 < warning < autocompact < blocking < 1")
        if self.tool_result_preview_chars >= self.tool_result_max_chars:
            raise ValueError("tool_result_preview_chars must be smaller than tool_result_max_chars")
        if self.history_snip_target_chars >= self.history_snip_trigger_chars:
            raise ValueError("history_snip_target_chars must be smaller than history_snip_trigger_chars")
        if self.autocompact_trigger_chars <= self.autocompact_target_chars:
            raise ValueError("autocompact_trigger_chars must be greater than autocompact_target_chars")
        if self.autocompact_blocking_chars <= self.autocompact_trigger_chars:
            raise ValueError("autocompact_blocking_chars must be greater than autocompact_trigger_chars")
        _require_non_empty_names("history_snip_tool_names", self.history_snip_tool_names)
        _require_non_empty_names("microcompact_tool_names", self.microcompact_tool_names)
