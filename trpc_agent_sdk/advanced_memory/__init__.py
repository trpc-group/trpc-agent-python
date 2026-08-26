# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Optional Advanced Memory module that leaves the legacy mechanism unchanged."""

from ._autocompact import AutoCompact
from ._autocompact import AutoCompactCallback
from ._autocompact import AutoCompactResult
from ._autocompact import content_signature
from ._autocompact import ForkedLegacySummaryGenerator
from ._autocompact import setup_autocompact
from ._config import AdvancedMemoryConfig
from ._formats import MemoryDocument
from ._formats import MemoryIndexEntry
from ._formats import MemoryType
from ._formats import memory_freshness
from ._formats import parse_memory_updated_at
from ._formats import SESSION_MEMORY_SECTION_DESCRIPTIONS
from ._formats import SESSION_MEMORY_SECTIONS
from ._formats import SessionMemoryDocument
from ._history_snip import estimate_request_chars
from ._history_snip import HistorySnip
from ._history_snip import HistorySnipCallback
from ._history_snip import HistorySnipResult
from ._history_snip import setup_history_snip
from ._memory_context import LongTermMemoryContext
from ._memory_context import LongTermMemoryContextCallback
from ._memory_context import setup_long_term_memory_context
from ._microcompact import Microcompact
from ._microcompact import MicrocompactCallback
from ._microcompact import MicrocompactResult
from ._microcompact import setup_microcompact
from ._paths import AdvancedMemoryPaths
from ._preload_memory import MemoryCandidate
from ._preload_memory import MemoryPreloader
from ._preload_memory import MemoryRelevanceSelector
from ._preload_memory import ModelMemoryRelevanceSelector
from ._preload_memory import select_relevant_memory_filenames
from ._runtime import AdvancedMemoryRuntime
from ._session_memory import build_session_memory_prompt
from ._session_memory import ForkedSessionMemoryGenerator
from ._session_memory import has_session_memory_content
from ._session_memory import limit_session_memory_document
from ._session_memory import SessionMemoryExtractionInput
from ._session_memory import SessionMemoryExtractionResult
from ._session_memory import SessionMemoryExtractor
from ._session_service import TranscriptSessionService
from ._integration import AdvancedContextManagement
from ._integration import AdvancedMemoryIntegration
from ._integration import setup_advanced_memory
from ._integration import setup_context_management
from ._storage import LongTermMemoryStore
from ._storage import SessionMemoryStore
from ._storage import ToolResultStore
from ._storage import TranscriptStore
from ._tool_result_budget import setup_tool_result_budget
from ._tool_result_budget import ToolResultBudget
from ._tool_result_budget import ToolResultBudgetCallback
from ._tool_result_budget import ToolResultBudgetResult
from ._transcript import TRANSCRIPT_SCHEMA_VERSION
from ._token_budget import ContextBudget
from ._token_budget import ContextTokenEstimate
from ._token_budget import HeuristicTokenEstimator
from ._token_budget import ModelContextWindowResolver
from ._token_budget import TokenContextTracker
from ._token_budget import TokenEstimator

__all__ = [
    "AutoCompact",
    "AutoCompactCallback",
    "AutoCompactResult",
    "AdvancedMemoryConfig",
    "AdvancedContextManagement",
    "AdvancedMemoryIntegration",
    "AdvancedMemoryPaths",
    "AdvancedMemoryRuntime",
    "ContextBudget",
    "ContextTokenEstimate",
    "build_session_memory_prompt",
    "content_signature",
    "estimate_request_chars",
    "ForkedLegacySummaryGenerator",
    "ForkedSessionMemoryGenerator",
    "has_session_memory_content",
    "HistorySnip",
    "HistorySnipCallback",
    "HistorySnipResult",
    "HeuristicTokenEstimator",
    "LongTermMemoryStore",
    "LongTermMemoryContext",
    "LongTermMemoryContextCallback",
    "MemoryDocument",
    "MemoryIndexEntry",
    "MemoryType",
    "MemoryCandidate",
    "MemoryPreloader",
    "MemoryRelevanceSelector",
    "ModelMemoryRelevanceSelector",
    "select_relevant_memory_filenames",
    "memory_freshness",
    "parse_memory_updated_at",
    "Microcompact",
    "MicrocompactCallback",
    "MicrocompactResult",
    "ModelContextWindowResolver",
    "SESSION_MEMORY_SECTION_DESCRIPTIONS",
    "SESSION_MEMORY_SECTIONS",
    "SessionMemoryDocument",
    "SessionMemoryExtractionInput",
    "SessionMemoryExtractionResult",
    "SessionMemoryExtractor",
    "SessionMemoryStore",
    "TRANSCRIPT_SCHEMA_VERSION",
    "ToolResultBudget",
    "ToolResultBudgetCallback",
    "ToolResultBudgetResult",
    "ToolResultStore",
    "TokenContextTracker",
    "TokenEstimator",
    "TranscriptSessionService",
    "TranscriptStore",
    "setup_autocompact",
    "setup_advanced_memory",
    "limit_session_memory_document",
    "setup_history_snip",
    "setup_context_management",
    "setup_long_term_memory_context",
    "setup_microcompact",
    "setup_tool_result_budget",
]
