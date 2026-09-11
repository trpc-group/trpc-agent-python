# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Canonical context-compression package for session management."""

from trpc_agent_sdk.abc import CompactTrigger

from .advanced import AdvancedAutoCompactSummarizer
from .advanced import AdvancedAutoCompactSummarizerManager
from .advanced import BaseCompactSummarizerHandler
from .advanced import BaseTokenEstimator
from .advanced import BaseModelContextWindowResolver
from .advanced import AutoCompactSummarizerConfig
from .advanced import HistorySnipConfig
from .advanced import TokenContextTrackerConfig
from .advanced import MicroCompactConfig
from .advanced import ToolResultBudgetConfig
from .advanced import SessionMemoryExtractorConfig
from .advanced import AdvancedAutoCompactSummarizerConfig
from .advanced import AdvancedAutoCompactSummarizerFilter
from .advanced import HistorySnip
from .advanced import MicroCompact
from .advanced import SessionMemoryDocument
from .advanced import SessionMemoryExtractor
from .advanced import AdvancedAutoCompactSummarizerRuntime
from .advanced import TokenContextTracker
from .advanced import ToolResultBudget
from .default import DEFAULT_SUMMARIZER_PROMPT
from .default import DefaultSessionSummarizer
from .default import DefaultSessionSummarizerManager
from .default import DefaultSessionSummary
from .default import CheckSummarizerFunction
from .default import set_summarizer_token_threshold
from .default import set_summarizer_events_count_threshold
from .default import set_summarizer_time_interval_threshold
from .default import set_summarizer_important_content_threshold
from .default import set_summarizer_conversation_threshold
from .default import set_summarizer_check_functions_by_and
from .default import set_summarizer_check_functions_by_or

__all__ = [
    "CompactTrigger",
    "AdvancedAutoCompactSummarizer",
    "AdvancedAutoCompactSummarizerManager",
    "BaseCompactSummarizerHandler",
    "BaseTokenEstimator",
    "BaseModelContextWindowResolver",
    "AutoCompactSummarizerConfig",
    "HistorySnipConfig",
    "TokenContextTrackerConfig",
    "MicroCompactConfig",
    "ToolResultBudgetConfig",
    "SessionMemoryExtractorConfig",
    "AdvancedAutoCompactSummarizerConfig",
    "AdvancedAutoCompactSummarizerFilter",
    "HistorySnip",
    "MicroCompact",
    "SessionMemoryDocument",
    "SessionMemoryExtractor",
    "AdvancedAutoCompactSummarizerRuntime",
    "TokenContextTracker",
    "ToolResultBudget",
    "DEFAULT_SUMMARIZER_PROMPT",
    "DefaultSessionSummarizer",
    "DefaultSessionSummarizerManager",
    "DefaultSessionSummary",
    "CheckSummarizerFunction",
    "set_summarizer_token_threshold",
    "set_summarizer_events_count_threshold",
    "set_summarizer_time_interval_threshold",
    "set_summarizer_important_content_threshold",
    "set_summarizer_conversation_threshold",
    "set_summarizer_check_functions_by_and",
    "set_summarizer_check_functions_by_or",
]
