# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Planner package for TRPC Agent framework.

This package provides planning capabilities for agents, allowing them to
structure their thinking and reasoning processes before taking actions.

Classes:
    BasePlanner: Abstract base class for all planners
    BuiltInPlanner: Uses model's built-in thinking features
    PlanReActPlanner: Enforces structured Plan-Reasoning-Action workflow
"""

from trpc_agent_sdk.abc import PlannerABC as BasePlanner

from ._built_in_planner import BuiltInPlanner
from ._plan_re_act_planner import PlanReActPlanner
from ._planning_processor import PlanningProcessor
from ._planning_processor import default_planning_processor

__all__ = [
    "BasePlanner",
    "BuiltInPlanner",
    "PlanReActPlanner",
    "PlanningProcessor",
    "default_planning_processor",
]
