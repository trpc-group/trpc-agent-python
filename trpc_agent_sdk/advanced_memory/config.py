"""Configuration for the independent Advanced Memory mechanism."""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
import os
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
        if self.memory_index_max_lines <= 0:
            raise ValueError("memory_index_max_lines must be greater than zero")
        if self.memory_index_max_bytes <= 0:
            raise ValueError("memory_index_max_bytes must be greater than zero")
        if self.preload_memory_max_topics <= 0:
            raise ValueError("preload_memory_max_topics must be greater than zero")
        if self.preload_memory_max_chars <= 0:
            raise ValueError("preload_memory_max_chars must be greater than zero")
        if self.preload_memory_candidate_limit <= 0:
            raise ValueError("preload_memory_candidate_limit must be greater than zero")
        for value in (
                self.memory_dir_name,
                self.session_dir_name,
                self.memory_index_name,
                self.transcript_name,
                self.session_memory_name,
        ):
            if not value or Path(value).name != value:
                raise ValueError(f"Invalid memory path component: {value!r}")
        if self.tool_result_max_chars <= 0:
            raise ValueError("tool_result_max_chars must be greater than zero")
        if self.tool_results_per_message_max_chars <= 0:
            raise ValueError("tool_results_per_message_max_chars must be greater than zero")
        if self.tool_result_preview_chars <= 0:
            raise ValueError("tool_result_preview_chars must be greater than zero")
        if self.tool_result_preview_chars >= self.tool_result_max_chars:
            raise ValueError("tool_result_preview_chars must be smaller than tool_result_max_chars")
        if self.history_snip_trigger_chars <= 0:
            raise ValueError("history_snip_trigger_chars must be greater than zero")
        if self.history_snip_target_chars <= 0:
            raise ValueError("history_snip_target_chars must be greater than zero")
        if self.history_snip_target_chars >= self.history_snip_trigger_chars:
            raise ValueError("history_snip_target_chars must be smaller than history_snip_trigger_chars")
        if self.history_snip_keep_recent <= 0:
            raise ValueError("history_snip_keep_recent must be greater than zero")
        if not self.history_snip_tool_names or any(not name.strip() for name in self.history_snip_tool_names):
            raise ValueError("history_snip_tool_names must contain non-empty names")
        if self.model_context_window_tokens is not None and self.model_context_window_tokens <= 0:
            raise ValueError("model_context_window_tokens must be greater than zero when provided")
        if self.max_output_tokens < 0:
            raise ValueError("max_output_tokens must not be negative")
        if self.model_context_window_tokens is not None and self.max_output_tokens >= self.model_context_window_tokens:
            raise ValueError("max_output_tokens must be smaller than model_context_window_tokens")
        if not (0 < self.token_warning_ratio < self.token_autocompact_ratio < self.token_blocking_ratio < 1):
            raise ValueError("token ratios must satisfy 0 < warning < autocompact < blocking < 1")
        if self.session_memory_initial_chars <= 0:
            raise ValueError("session_memory_initial_chars must be greater than zero")
        if self.session_memory_update_chars <= 0:
            raise ValueError("session_memory_update_chars must be greater than zero")
        if self.session_memory_initial_tokens <= 0:
            raise ValueError("session_memory_initial_tokens must be greater than zero")
        if self.session_memory_update_tokens <= 0:
            raise ValueError("session_memory_update_tokens must be greater than zero")
        if self.session_memory_tool_calls_between_updates <= 0:
            raise ValueError("session_memory_tool_calls_between_updates must be greater than zero")
        if self.session_memory_prompt_max_chars <= 0:
            raise ValueError("session_memory_prompt_max_chars must be greater than zero")
        if self.session_memory_request_overhead_tokens < 0:
            raise ValueError("session_memory_request_overhead_tokens must not be negative")
        if self.session_memory_section_max_chars <= 0:
            raise ValueError("session_memory_section_max_chars must be greater than zero")
        if self.session_memory_total_max_chars <= 0:
            raise ValueError("session_memory_total_max_chars must be greater than zero")
        if self.session_memory_wait_timeout_seconds <= 0:
            raise ValueError("session_memory_wait_timeout_seconds must be greater than zero")
        if self.autocompact_target_chars <= 0:
            raise ValueError("autocompact_target_chars must be greater than zero")
        if self.autocompact_trigger_chars <= self.autocompact_target_chars:
            raise ValueError("autocompact_trigger_chars must be greater than autocompact_target_chars")
        if self.autocompact_blocking_chars <= self.autocompact_trigger_chars:
            raise ValueError("autocompact_blocking_chars must be greater than autocompact_trigger_chars")
        if self.autocompact_keep_recent_contents <= 0:
            raise ValueError("autocompact_keep_recent_contents must be greater than zero")
        if self.autocompact_max_failures <= 0:
            raise ValueError("autocompact_max_failures must be greater than zero")
        if self.autocompact_summary_input_max_chars <= 0:
            raise ValueError("autocompact_summary_input_max_chars must be greater than zero")
        if self.autocompact_summary_retries <= 0:
            raise ValueError("autocompact_summary_retries must be greater than zero")
        if self.microcompact_gap_seconds <= 0:
            raise ValueError("microcompact_gap_seconds must be greater than zero")
        if self.microcompact_trigger_count <= 0:
            raise ValueError("microcompact_trigger_count must be greater than zero")
        if self.microcompact_keep_recent <= 0:
            raise ValueError("microcompact_keep_recent must be greater than zero")
        if self.microcompact_keep_recent >= self.microcompact_trigger_count:
            raise ValueError("microcompact_keep_recent must be smaller than microcompact_trigger_count")
        if not self.microcompact_tool_names or any(not name.strip() for name in self.microcompact_tool_names):
            raise ValueError("microcompact_tool_names must contain non-empty names")
        object.__setattr__(self, "root_dir", self.root_dir.expanduser().resolve())
