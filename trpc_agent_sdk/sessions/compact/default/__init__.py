# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Default compact session manager."""

from ._checker import CheckSummarizerFunction
from ._checker import set_summarizer_token_threshold
from ._checker import set_summarizer_events_count_threshold
from ._checker import set_summarizer_time_interval_threshold
from ._checker import set_summarizer_important_content_threshold
from ._checker import set_summarizer_conversation_threshold
from ._checker import set_summarizer_check_functions_by_and
from ._checker import set_summarizer_check_functions_by_or
from ._summarizer import DEFAULT_SUMMARIZER_PROMPT
from ._summarizer import DefaultSessionSummary
from ._summarizer import DefaultSessionSummarizer
from ._summarizer_manager import DefaultSessionSummarizerManager

__all__ = [
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
