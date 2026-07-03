# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Coding assistant configurations demonstrating SpawnSubAgentTool.

Three configurations are provided, selectable via ``--mode`` on the
command line:

- ``default`` — zero-config: ``SpawnSubAgentTool()``. The ``default``
  archetype (neutral task executor, inherits the assistant's tools) is the only
  auto-registered archetype.
- ``code`` — ``security-auditor`` defined in code via ``SubAgentArchetype``,
  alongside built-in ``Explore`` / ``Plan``.
- ``md`` — ``security-auditor`` loaded from ``.trpc_agents/security-auditor.md``,
  alongside built-in ``Explore`` / ``Plan``, showing how MD-defined and
  built-in archetypes co-exist.
"""

import os

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.agents.sub_agent import EXPLORE_AGENT
from trpc_agent_sdk.agents.sub_agent import PLAN_AGENT
from trpc_agent_sdk.agents.sub_agent import SubAgentArchetype
from trpc_agent_sdk.tools import SpawnSubAgentTool
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import GlobTool
from trpc_agent_sdk.tools import GrepTool
from trpc_agent_sdk.tools import ReadTool

from .config import get_model_config
from .prompts import INSTRUCTION


def _create_model() -> LLMModel:
    api_key, url, model_name = get_model_config()
    return OpenAIModel(model_name=model_name, api_key=api_key, base_url=url)


def create_default_agent() -> LlmAgent:
    """Zero-config coding assistant: only the ``default`` archetype is registered.

    Simple tasks are handled directly. Complex tasks are dispatched
    to the ``default`` sub-agent, which inherits the assistant's tools.
    """
    return LlmAgent(
        name="coding_assistant",
        description="Coding assistant with spawn_subagent in zero-config mode.",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[ReadTool(), GlobTool(), GrepTool(), SpawnSubAgentTool()],
    )


_SECURITY_AUDITOR = SubAgentArchetype(
    name="security-auditor",
    description=(
        "Specialized security auditor for code vulnerability analysis. "
        "Use this for ANY security-related task: code audits, secret "
        "detection, auth review. Checks for OWASP Top 10 risks, CWE "
        "patterns, rates severity, and produces structured reports."
    ),
    instruction=(
        "You are a security auditor. Review the relevant code for security "
        "issues: injection risks, hardcoded secrets, unsafe API usage, "
        "missing authentication/authorization checks. Report findings "
        "concisely with severity (low/medium/high/critical). Do NOT modify files."
    ),
    tools=(ReadTool, GlobTool, GrepTool),
)


def create_code_agent() -> LlmAgent:
    """Coding assistant with code-defined security-auditor + built-in Explore/Plan.

    Simple tasks are handled directly. Security review tasks are auto-routed
    to ``security-auditor``, code exploration to ``Explore``, and planning
    tasks to ``Plan``. ``default`` serves as fallback.
    """
    return LlmAgent(
        name="coding_assistant",
        description="Coding assistant with Explore, Plan, and custom archetypes.",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[
            ReadTool(), GlobTool(), GrepTool(),
            SpawnSubAgentTool(agents=[_SECURITY_AUDITOR, EXPLORE_AGENT, PLAN_AGENT]),
        ],
    )


_AGENTS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".trpc_agents")


def create_md_agent() -> LlmAgent:
    """Coding assistant with MD-defined security-auditor + built-in Explore/Plan.

    Simple tasks are handled directly. Security review tasks are auto-routed
    to the MD-defined ``security-auditor``. ``default`` serves as fallback.
    """
    return LlmAgent(
        name="coding_assistant",
        description="Coding assistant with Explore, Plan, and MD-defined archetype.",
        model=_create_model(),
        instruction=INSTRUCTION,
        tools=[
            ReadTool(), GlobTool(), GrepTool(),
            SpawnSubAgentTool(agents=[EXPLORE_AGENT, PLAN_AGENT], agent_paths=[_AGENTS_PATH]),
        ],
    )


root_agent = create_default_agent()
