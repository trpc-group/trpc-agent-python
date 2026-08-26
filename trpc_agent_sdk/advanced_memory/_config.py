# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Configuration for the independent Advanced Memory mechanism."""

from __future__ import annotations

import os
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any

DEFAULT_COMPACTABLE_TOOL_NAMES = (
    "Read",
    "Bash",
    "Grep",
    "Glob",
    "WebSearch",
    "WebFetch",
    "Edit",
    "Write",
)


def _integer_from_environment(
    name: str,
    *,
    default: int | None,
    minimum: int,
) -> int | None:
    """Read and validate an optional integer setting from the environment."""
    raw_value = os.environ.get(name, "").strip()
    if not raw_value:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        description = "positive integer" if minimum > 0 else "non-negative integer"
        raise ValueError(f"{name} must be a {description}") from exc
    if value < minimum:
        description = "positive integer" if minimum > 0 else "non-negative integer"
        raise ValueError(f"{name} must be a {description}")
    return value


def _require_positive(**values: int | float) -> None:
    """Require each named numeric setting to be greater than zero."""
    for name, value in values.items():
        if value <= 0:
            raise ValueError(f"{name} must be greater than zero")


def _require_non_negative(**values: int | float) -> None:
    """Require each named numeric setting to be non-negative."""
    for name, value in values.items():
        if value < 0:
            raise ValueError(f"{name} must not be negative")


def _require_less_than(
    name: str,
    value: int | float,
    upper_name: str,
    upper_value: int | float,
) -> None:
    """Require one named numeric setting to be smaller than another."""
    if value >= upper_value:
        raise ValueError(f"{name} must be smaller than {upper_name}")


def _require_greater_than(
    name: str,
    value: int | float,
    lower_name: str,
    lower_value: int | float,
) -> None:
    """Require one named numeric setting to be greater than another."""
    if value <= lower_value:
        raise ValueError(f"{name} must be greater than {lower_name}")


def _require_non_empty_names(name: str, values: tuple[str, ...]) -> None:
    """Require a non-empty sequence containing only non-empty names."""
    if not values or any(not value.strip() for value in values):
        raise ValueError(f"{name} must contain non-empty names")


def _validate_path_components(values: tuple[str, ...]) -> None:
    """Require safe, single-component names for memory storage paths."""
    for value in values:
        if not value or Path(value).name != value:
            raise ValueError(f"Invalid memory path component: {value!r}")


@dataclass(frozen=True)
class AdvancedMemoryConfig:
    """Configure the independent memory directory and storage limits."""

    enabled: bool = True
    root_dir: Path = field(default_factory=Path.cwd)
    memory_dir_name: str = "MEMORY"
    session_dir_name: str = "SESSION"
    memory_index_name: str = "MEMORY.md"
    transcript_name: str = "transcript.jsonl"
    session_memory_name: str = "session_memory.md"
    memory_index_max_lines: int = 200
    memory_index_max_bytes: int = 25_000
    long_term_memory_injection_enabled: bool = True
    tool_result_max_chars: int = 50_000
    tool_results_per_message_max_chars: int = 200_000
    tool_result_preview_chars: int = 2_000
    history_snip_enabled: bool = True
    history_snip_trigger_chars: int = 600_000
    history_snip_target_chars: int = 400_000
    history_snip_keep_recent: int = 5
    history_snip_tool_names: tuple[str, ...] = DEFAULT_COMPACTABLE_TOOL_NAMES
    model_context_window_tokens: int | None = field(default_factory=lambda: _integer_from_environment(
        "TRPC_AGENT_MODEL_CONTEXT_WINDOW_TOKENS",
        default=None,
        minimum=1,
    ))
    max_output_tokens: int = field(default_factory=lambda: _integer_from_environment(
        "TRPC_AGENT_MAX_OUTPUT_TOKENS",
        default=0,
        minimum=0,
    ))
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
    encoding: str = "utf-8"
    transcript_fsync: bool = False
    preload_memory_enabled: bool = False
    preload_memory_max_topics: int = 5
    preload_memory_max_chars: int = 50_000
    preload_memory_candidate_limit: int = 200

    def __post_init__(self) -> None:
        """Validate the configuration and normalize the root directory."""
        _require_positive(
            memory_index_max_lines=self.memory_index_max_lines,
            memory_index_max_bytes=self.memory_index_max_bytes,
            preload_memory_max_topics=self.preload_memory_max_topics,
            preload_memory_max_chars=self.preload_memory_max_chars,
            preload_memory_candidate_limit=self.preload_memory_candidate_limit,
        )
        _validate_path_components((
            self.memory_dir_name,
            self.session_dir_name,
            self.memory_index_name,
            self.transcript_name,
            self.session_memory_name,
        ))
        _require_positive(
            tool_result_max_chars=self.tool_result_max_chars,
            tool_results_per_message_max_chars=self.tool_results_per_message_max_chars,
            tool_result_preview_chars=self.tool_result_preview_chars,
        )
        _require_less_than(
            "tool_result_preview_chars",
            self.tool_result_preview_chars,
            "tool_result_max_chars",
            self.tool_result_max_chars,
        )
        _require_positive(
            history_snip_trigger_chars=self.history_snip_trigger_chars,
            history_snip_target_chars=self.history_snip_target_chars,
        )
        _require_less_than(
            "history_snip_target_chars",
            self.history_snip_target_chars,
            "history_snip_trigger_chars",
            self.history_snip_trigger_chars,
        )
        _require_positive(history_snip_keep_recent=self.history_snip_keep_recent)
        _require_non_empty_names("history_snip_tool_names", self.history_snip_tool_names)
        if self.model_context_window_tokens is not None and self.model_context_window_tokens <= 0:
            raise ValueError("model_context_window_tokens must be greater than zero when provided")
        _require_non_negative(max_output_tokens=self.max_output_tokens)
        if self.model_context_window_tokens is not None and self.max_output_tokens >= self.model_context_window_tokens:
            raise ValueError("max_output_tokens must be smaller than model_context_window_tokens")
        if not (0 < self.token_warning_ratio < self.token_autocompact_ratio < self.token_blocking_ratio < 1):
            raise ValueError("token ratios must satisfy 0 < warning < autocompact < blocking < 1")
        _require_positive(
            session_memory_initial_chars=self.session_memory_initial_chars,
            session_memory_update_chars=self.session_memory_update_chars,
            session_memory_initial_tokens=self.session_memory_initial_tokens,
            session_memory_update_tokens=self.session_memory_update_tokens,
            session_memory_tool_calls_between_updates=self.session_memory_tool_calls_between_updates,
            session_memory_prompt_max_chars=self.session_memory_prompt_max_chars,
        )
        _require_non_negative(session_memory_request_overhead_tokens=self.session_memory_request_overhead_tokens)
        _require_positive(
            session_memory_section_max_chars=self.session_memory_section_max_chars,
            session_memory_total_max_chars=self.session_memory_total_max_chars,
            session_memory_wait_timeout_seconds=self.session_memory_wait_timeout_seconds,
        )
        _require_positive(autocompact_target_chars=self.autocompact_target_chars)
        _require_greater_than(
            "autocompact_trigger_chars",
            self.autocompact_trigger_chars,
            "autocompact_target_chars",
            self.autocompact_target_chars,
        )
        _require_greater_than(
            "autocompact_blocking_chars",
            self.autocompact_blocking_chars,
            "autocompact_trigger_chars",
            self.autocompact_trigger_chars,
        )
        _require_positive(
            autocompact_keep_recent_contents=self.autocompact_keep_recent_contents,
            autocompact_max_failures=self.autocompact_max_failures,
            autocompact_summary_input_max_chars=self.autocompact_summary_input_max_chars,
            autocompact_summary_retries=self.autocompact_summary_retries,
        )
        _require_positive(
            microcompact_gap_seconds=self.microcompact_gap_seconds,
            microcompact_trigger_count=self.microcompact_trigger_count,
            microcompact_keep_recent=self.microcompact_keep_recent,
        )
        _require_less_than(
            "microcompact_keep_recent",
            self.microcompact_keep_recent,
            "microcompact_trigger_count",
            self.microcompact_trigger_count,
        )
        _require_non_empty_names("microcompact_tool_names", self.microcompact_tool_names)
        object.__setattr__(self, "root_dir", self.root_dir.expanduser().resolve())
