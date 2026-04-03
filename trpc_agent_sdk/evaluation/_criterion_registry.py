# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Criterion type and registry. Register a custom match rule to replace built-in logic."""

from __future__ import annotations

from enum import Enum
from typing import Any
from typing import Callable
from typing import Optional

from ._eval_criterion import FinalResponseCriterion
from ._eval_criterion import JSONCriterion
from ._eval_criterion import TextCriterion
from ._eval_criterion import ToolTrajectoryCriterion
from ._llm_criterion import LLMJudgeCriterion


class CriterionType(str, Enum):
    """Built-in criterion types. Use with CRITERION_REGISTRY.register(type, match_fn)."""
    TEXT = "text"
    JSON = "json"
    TOOL_TRAJECTORY = "tool_trajectory"
    FINAL_RESPONSE = "final_response"
    LLM_JUDGE = "llm_judge"


# (actual, expected) -> bool
MatchRule = Callable[[Any, Any], bool]


class _ReplaceWrapper:

    def __init__(self, match_rule: MatchRule) -> None:
        self._match_rule = match_rule

    def matches(self, actual: Any, expected: Any) -> bool:
        return self._match_rule(actual, expected)


class CriterionRegistry:
    """Register a custom match rule to replace built-in criterion for a type.

    Example:
        from trpc_agent_sdk.evaluation import CriterionType, CRITERION_REGISTRY

        def my_text_match(actual: str, expected: str) -> bool:
            return actual.strip().lower() == expected.strip().lower()

        CRITERION_REGISTRY.register(CriterionType.TEXT, my_text_match)
    """

    def __init__(self) -> None:
        self._overrides: dict[str, MatchRule] = {}
        self._factories = {
            "tool_trajectory": ToolTrajectoryCriterion.from_dict,
            "toolTrajectory": ToolTrajectoryCriterion.from_dict,
            "final_response": FinalResponseCriterion.from_dict,
            "finalResponse": FinalResponseCriterion.from_dict,
            "text": TextCriterion.from_dict,
            "json": JSONCriterion.from_dict,
            "llm_judge": LLMJudgeCriterion.from_dict,
            "llmJudge": LLMJudgeCriterion.from_dict,
        }

    def register(self, criterion_type: CriterionType, match_rule: MatchRule) -> None:
        """Register a custom match rule for the criterion type. Replaces built-in matching."""
        key = criterion_type.value if isinstance(criterion_type, CriterionType) else str(criterion_type)
        self._overrides[key] = match_rule

    def build(self, config: dict | None, metric_key: Optional[str] = None) -> Any:
        """Build criterion from config. If a match rule was registered for this type, use it."""
        if not config or not isinstance(config, dict):
            return None

        type_name: Optional[str] = None
        sub_config: Optional[dict] = None

        explicit_type = config.get("type") or config.get("Type")
        if explicit_type:
            type_name = str(explicit_type).lower().replace("-", "_")
            sub_config = config
        elif metric_key == "tool_trajectory_avg_score":
            type_name = "tool_trajectory"
            sub_config = config.get("toolTrajectory") or config.get("tool_trajectory")
        elif metric_key == "final_response_avg_score":
            type_name = "final_response"
            sub_config = config.get("finalResponse") or config.get("final_response")
        elif metric_key in ("llm_final_response", "llm_rubric_response", "llm_rubric_knowledge_recall"):
            type_name = "llm_judge"
            sub_config = config.get("llmJudge") or config.get("llm_judge")

        if not type_name:
            return None

        override = self._overrides.get(type_name)
        if override is not None:
            return _ReplaceWrapper(override)

        if sub_config and isinstance(sub_config, dict):
            factory = self._factories.get(type_name)
            if factory:
                return factory(sub_config)
        return None


# Default singleton: register custom match via CRITERION_REGISTRY.register(CriterionType.XXX, fn).
CRITERION_REGISTRY = CriterionRegistry()
