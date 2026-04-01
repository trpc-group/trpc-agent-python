# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
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
from ._in_memory_session_service import InMemorySessionService
from ._in_memory_session_service import SessionWithTTL
from ._in_memory_session_service import StateWithTTL
from ._redis_session_service import RedisSessionService
from ._session import Session
from ._session_summarizer import SessionSummarizer
from ._session_summarizer import SessionSummary
from ._sql_session_service import SessionStorageBase
from ._sql_session_service import SessionStorageEvent
from ._sql_session_service import SqlSessionService
from ._sql_session_service import StorageAppState
from ._sql_session_service import StorageSession
from ._sql_session_service import StorageUserState
from ._summarizer_checker import CheckSummarizerFunction
from ._summarizer_checker import set_summarizer_check_functions_by_and
from ._summarizer_checker import set_summarizer_check_functions_by_or
from ._summarizer_checker import set_summarizer_conversation_threshold
from ._summarizer_checker import set_summarizer_events_count_threshold
from ._summarizer_checker import set_summarizer_important_content_threshold
from ._summarizer_checker import set_summarizer_time_interval_threshold
from ._summarizer_checker import set_summarizer_token_threshold
from ._summarizer_manager import SummarizerSessionManager
from ._types import SessionServiceConfig
from ._utils import StateStorageEntry
from ._utils import app_state_key
from ._utils import extract_state_delta
from ._utils import merge_state
from ._utils import session_key
from ._utils import user_state_key

__all__ = [
    "ListSessionsResponse",
    "State",
    "BaseSessionService",
    "HistoryRecord",
    "InMemorySessionService",
    "SessionWithTTL",
    "StateWithTTL",
    "RedisSessionService",
    "Session",
    "SessionSummarizer",
    "SessionSummary",
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
    "SummarizerSessionManager",
    "SessionServiceConfig",
    "StateStorageEntry",
    "app_state_key",
    "extract_state_delta",
    "merge_state",
    "session_key",
    "user_state_key",
]
