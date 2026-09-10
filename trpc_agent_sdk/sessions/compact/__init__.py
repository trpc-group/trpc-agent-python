# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Canonical context-compression package for session management."""

from ._autocompact import AutoCompact
from ._autocompact import AutoCompactCallback
from ._autocompact import AutoCompactResult
from ._autocompact import content_signature
from ._autocompact import ForkedLegacySummaryGenerator
from ._autocompact import setup_autocompact
from ._base_manager import BaseSessionCompactManager
from ._base_config import BaseSessionCompactConfig
from ._config import AdvancedCompactConfig
from ._formats import build_session_memory_state
from ._formats import parse_session_memory_state
from ._formats import SESSION_MEMORY_SECTION_DESCRIPTIONS
from ._formats import SESSION_MEMORY_SECTIONS
from ._formats import SESSION_MEMORY_STATE_KEY
from ._formats import SessionMemoryDocument
from ._history_snip import estimate_request_chars
from ._history_snip import HistorySnip
from ._history_snip import HistorySnipCallback
from ._history_snip import HistorySnipResult
from ._history_snip import setup_history_snip
from ._integration import setup_advanced_session_compact
from ._integration import setup_context_compression
from ._manager import AdvancedSessionCompactManager
from ._microcompact import Microcompact
from ._microcompact import MicrocompactCallback
from ._microcompact import MicrocompactResult
from ._microcompact import setup_microcompact
from ._paths import AdvancedMemoryPaths
from ._paths import MemoryScope
from ._runtime import AdvancedMemoryRuntime
from ._runtime import ScopedAdvancedMemoryRuntime
from ._session_memory import build_session_memory_prompt
from ._session_memory import ForkedSessionMemoryGenerator
from ._session_memory import has_session_memory_content
from ._session_memory import limit_session_memory_document
from ._session_memory import SessionMemoryExtractionInput
from ._session_memory import SessionMemoryExtractionResult
from ._session_memory import SessionMemoryExtractor
from ._session_service import TranscriptSessionService
from ._storage import SessionMemoryStore
from ._storage import ToolResultStore
from ._storage import TranscriptStore
from ._token_budget import ContextBudget
from ._token_budget import ContextTokenEstimate
from ._token_budget import HeuristicTokenEstimator
from ._token_budget import ModelContextWindowResolver
from ._token_budget import TokenContextTracker
from ._token_budget import TokenEstimator
from ._tool_result_budget import setup_tool_result_budget
from ._tool_result_budget import ToolResultBudget
from ._tool_result_budget import ToolResultBudgetCallback
from ._tool_result_budget import ToolResultBudgetResult
from ._transcript import TRANSCRIPT_SCHEMA_VERSION

__all__ = [
    "AdvancedCompactConfig",
    "BaseSessionCompactConfig",
    "AdvancedMemoryPaths",
    "AdvancedMemoryRuntime",
    "AutoCompact",
    "AutoCompactCallback",
    "AutoCompactResult",
    "ContextBudget",
    "ContextTokenEstimate",
    "ForkedLegacySummaryGenerator",
    "ForkedSessionMemoryGenerator",
    "HeuristicTokenEstimator",
    "HistorySnip",
    "HistorySnipCallback",
    "HistorySnipResult",
    "MemoryScope",
    "Microcompact",
    "MicrocompactCallback",
    "MicrocompactResult",
    "ModelContextWindowResolver",
    "ScopedAdvancedMemoryRuntime",
    "SESSION_MEMORY_SECTION_DESCRIPTIONS",
    "SESSION_MEMORY_SECTIONS",
    "SESSION_MEMORY_STATE_KEY",
    "SessionMemoryDocument",
    "SessionMemoryExtractionInput",
    "SessionMemoryExtractionResult",
    "SessionMemoryExtractor",
    "SessionMemoryStore",
    "BaseSessionCompactManager",
    "AdvancedSessionCompactManager",
    "TokenContextTracker",
    "TokenEstimator",
    "ToolResultBudget",
    "ToolResultBudgetCallback",
    "ToolResultBudgetResult",
    "ToolResultStore",
    "TRANSCRIPT_SCHEMA_VERSION",
    "TranscriptSessionService",
    "TranscriptStore",
    "build_session_memory_prompt",
    "build_session_memory_state",
    "content_signature",
    "estimate_request_chars",
    "has_session_memory_content",
    "limit_session_memory_document",
    "parse_session_memory_state",
    "setup_autocompact",
    "setup_advanced_session_compact",
    "setup_context_compression",
    "setup_history_snip",
    "setup_microcompact",
    "setup_tool_result_budget",
]
