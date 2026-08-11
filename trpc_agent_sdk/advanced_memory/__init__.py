"""Optional Advanced Memory module that leaves the legacy mechanism unchanged."""

from .autocompact import AutoCompact
from .autocompact import AutoCompactCallback
from .autocompact import AutoCompactResult
from .autocompact import content_signature
from .autocompact import ForkedLegacySummaryGenerator
from .autocompact import setup_autocompact
from .config import AdvancedMemoryConfig
from .formats import MemoryDocument
from .formats import MemoryIndexEntry
from .formats import MemoryType
from .formats import memory_freshness
from .formats import parse_memory_updated_at
from .formats import SESSION_MEMORY_SECTION_DESCRIPTIONS
from .formats import SESSION_MEMORY_SECTIONS
from .formats import SessionMemoryDocument
from .history_snip import estimate_request_chars
from .history_snip import HistorySnip
from .history_snip import HistorySnipCallback
from .history_snip import HistorySnipResult
from .history_snip import setup_history_snip
from .memory_context import LongTermMemoryContext
from .memory_context import LongTermMemoryContextCallback
from .memory_context import setup_long_term_memory_context
from .microcompact import Microcompact
from .microcompact import MicrocompactCallback
from .microcompact import MicrocompactResult
from .microcompact import setup_microcompact
from .paths import AdvancedMemoryPaths
from .preload_memory import MemoryCandidate
from .preload_memory import MemoryPreloader
from .preload_memory import MemoryRelevanceSelector
from .preload_memory import ModelMemoryRelevanceSelector
from .preload_memory import select_relevant_memory_filenames
from .runtime import AdvancedMemoryRuntime
from .session_memory import build_session_memory_prompt
from .session_memory import ForkedSessionMemoryGenerator
from .session_memory import has_session_memory_content
from .session_memory import limit_session_memory_document
from .session_memory import SessionMemoryExtractionInput
from .session_memory import SessionMemoryExtractionResult
from .session_memory import SessionMemoryExtractor
from .session_service import TranscriptSessionService
from .setup import AdvancedContextManagement
from .setup import AdvancedMemoryIntegration
from .setup import setup_advanced_memory
from .setup import setup_context_management
from .storage import LongTermMemoryStore
from .storage import SessionMemoryStore
from .storage import ToolResultStore
from .storage import TranscriptStore
from .tool_result_budget import setup_tool_result_budget
from .tool_result_budget import ToolResultBudget
from .tool_result_budget import ToolResultBudgetCallback
from .tool_result_budget import ToolResultBudgetResult
from .transcript import TRANSCRIPT_SCHEMA_VERSION
from .token_budget import ContextBudget
from .token_budget import ContextTokenEstimate
from .token_budget import HeuristicTokenEstimator
from .token_budget import ModelContextWindowResolver
from .token_budget import TokenContextTracker
from .token_budget import TokenEstimator

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
