# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Advanced compact session manager."""

from ._auto_compact import AdvancedAutoCompactSummarizer
from ._base import BaseCompactSummarizerHandler
from ._base import BaseTokenEstimator
from ._base import BaseModelContextWindowResolver
from ._config import AutoCompactSummarizerConfig
from ._config import HistorySnipConfig
from ._config import TokenContextTrackerConfig
from ._config import MicroCompactConfig
from ._config import ToolResultBudgetConfig
from ._config import SessionMemoryExtractorConfig
from ._config import AdvancedAutoCompactSummarizerConfig
from ._filters import AdvancedAutoCompactSummarizerFilter
from ._formats import SessionMemoryDocument
from ._history_snip import HistorySnip
from ._micro_compact import MicroCompact
from ._manager import AdvancedAutoCompactSummarizerManager
from ._compaction_memory_extractor import SessionMemoryExtractor
from ._runtime import AdvancedAutoCompactSummarizerRuntime
from ._token_budget import TokenContextTracker
from ._tool_result_budget import ToolResultBudget

__all__ = [
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
]
