# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""DSL to Python Graph code generator package.

This package provides a CLI entrypoint:

    python -m trpc_agent_dsl.codegen <workflow.json>
"""

import os

# Avoid trpc_agent_sdk reporter side-effects during codegen startup/import.
os.environ.setdefault("DISABLE_TRPC_AGENT_REPORT", "true")

from ._cli import ServiceMode
from ._cli import generate
from ._cli import generate_project
from ._cli import main
from ._render import render_workflow_files
from ._workflow import AgenticFilterFieldInfo
from ._workflow import CodeNodeConfig
from ._workflow import CompiledPredicate
from ._workflow import ConditionalCase
from ._workflow import ConditionalEdgeDefinition
from ._workflow import ConditionalRule
from ._workflow import EdgeDefinition
from ._workflow import EndNodeConfig
from ._workflow import ExecutorSpec
from ._workflow import Expression
from ._workflow import ExpressionReference
from ._workflow import KnowledgeConnectorSpec
from ._workflow import KnowledgeNodeConfig
from ._workflow import KnowledgeSearchToolSpec
from ._workflow import LLMNodeConfig
from ._workflow import MCPNodeConfig
from ._workflow import MCPToolSpec
from ._workflow import MemorySearchToolSpec
from ._workflow import ModelSpec
from ._workflow import NodeDefinition
from ._workflow import NodeOutputBinding
from ._workflow import SetStateAssignment
from ._workflow import SetStateConfig
from ._workflow import SkillsSpec
from ._workflow import StateVariableDefinition
from ._workflow import TransformNodeConfig
from ._workflow import UserApprovalConfig
from ._workflow import UserApprovalRouting
from ._workflow import WorkflowDefinition
from ._workflow import load_workflow_definition
from ._workflow import load_workflow_definition_from_json_text

__all__ = [
    "ServiceMode",
    "generate",
    "generate_project",
    "main",
    "render_workflow_files",
    "AgenticFilterFieldInfo",
    "CodeNodeConfig",
    "CompiledPredicate",
    "ConditionalCase",
    "ConditionalEdgeDefinition",
    "ConditionalRule",
    "EdgeDefinition",
    "EndNodeConfig",
    "ExecutorSpec",
    "Expression",
    "ExpressionReference",
    "KnowledgeConnectorSpec",
    "KnowledgeNodeConfig",
    "KnowledgeSearchToolSpec",
    "LLMNodeConfig",
    "MCPNodeConfig",
    "MCPToolSpec",
    "MemorySearchToolSpec",
    "ModelSpec",
    "NodeDefinition",
    "NodeOutputBinding",
    "SetStateAssignment",
    "SetStateConfig",
    "SkillsSpec",
    "StateVariableDefinition",
    "TransformNodeConfig",
    "UserApprovalConfig",
    "UserApprovalRouting",
    "WorkflowDefinition",
    "load_workflow_definition",
    "load_workflow_definition_from_json_text",
]
