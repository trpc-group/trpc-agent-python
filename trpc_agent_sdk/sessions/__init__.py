# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Session management module.

This module provides session management functionality including:
- Session data structures
- Abstract session service interfaces
- In-memory session service implementation
"""

from trpc_agent_sdk.abc import ListSessionsResponse
from trpc_agent_sdk.types import State

from ._base_session_service import BaseSessionService
from ._history_record import HistoryRecord
from .compact.default import DefaultSessionSummarizer
from .compact.default import DefaultSessionSummary
from .compact.default import DefaultSessionSummarizerManager
from .compact.default import CheckSummarizerFunction
from .compact.default import set_summarizer_check_functions_by_and
from .compact.default import set_summarizer_check_functions_by_or
from .compact.default import set_summarizer_conversation_threshold
from .compact.default import set_summarizer_events_count_threshold
from .compact.default import set_summarizer_important_content_threshold
from .compact.default import set_summarizer_time_interval_threshold
from .compact.default import set_summarizer_token_threshold
from .compact.advanced import AdvancedAutoCompactSummarizer
from .compact.advanced import AdvancedAutoCompactSummarizerManager
from .compact.advanced import BaseCompactSummarizerHandler
from .compact.advanced import BaseTokenEstimator
from .compact.advanced import BaseModelContextWindowResolver
from .compact.advanced import AutoCompactSummarizerConfig
from .compact.advanced import HistorySnipConfig
from .compact.advanced import TokenContextTrackerConfig
from .compact.advanced import MicroCompactConfig
from .compact.advanced import AdvancedAutoCompactSummarizerConfig
from ._in_memory_session_service import InMemorySessionService
from ._in_memory_session_service import SessionWithTTL
from ._in_memory_session_service import StateWithTTL
from ._redis_session_service import RedisSessionService
from ._redis_cluster_session_service import RedisClusterSessionService
from ._session import Session
from ._sql_session_service import SessionStorageBase
from ._sql_session_service import SessionStorageEvent
from ._sql_session_service import SqlSessionService
from ._sql_session_service import StorageAppState
from ._sql_session_service import StorageSession
from ._sql_session_service import StorageUserState
from ._types import SessionServiceConfig
from ._utils import StateStorageEntry
from ._utils import app_state_key
from ._utils import extract_state_delta
from ._utils import find_events_for_summary
from ._utils import is_summary_anchor
from ._utils import merge_state
from ._utils import session_key
from ._utils import user_state_key

# Default compact session summarizer for backward compatibility
SessionSummary = DefaultSessionSummary
SessionSummarizer = DefaultSessionSummarizer
SummarizerSessionManager = DefaultSessionSummarizerManager

__all__ = [
    "ListSessionsResponse",
    "State",
    "BaseSessionService",
    "HistoryRecord",
    "InMemorySessionService",
    "SessionWithTTL",
    "StateWithTTL",
    "RedisSessionService",
    "RedisClusterSessionService",
    "Session",
    "SessionStorageBase",
    "SessionStorageEvent",
    "SqlSessionService",
    "StorageAppState",
    "StorageSession",
    "StorageUserState",
    "CheckSummarizerFunction",
    "set_summarizer_check_functions_by_and",
    "set_summarizer_check_functions_by_or",
    "set_summarizer_conversation_threshold",
    "set_summarizer_events_count_threshold",
    "set_summarizer_important_content_threshold",
    "set_summarizer_time_interval_threshold",
    "set_summarizer_token_threshold",
    "DefaultSessionSummarizer",
    "DefaultSessionSummary",
    "DefaultSessionSummarizerManager",
    "SummarizerSessionManager",
    "SessionServiceConfig",
    "StateStorageEntry",
    "app_state_key",
    "extract_state_delta",
    "find_events_for_summary",
    "merge_state",
    "is_summary_anchor",
    "session_key",
    "user_state_key",
    "AdvancedAutoCompactSummarizer",
    "AdvancedAutoCompactSummarizerManager",
    "BaseCompactSummarizerHandler",
    "BaseTokenEstimator",
    "BaseModelContextWindowResolver",
    "AutoCompactSummarizerConfig",
    "HistorySnipConfig",
    "TokenContextTrackerConfig",
    "MicroCompactConfig",
    "AdvancedAutoCompactSummarizerConfig",
]
