# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""ClawAgent definition.

This module only defines and wires the agent objects — no runtime loop.
Runtime dispatch (session management, user I/O) is handled by the caller,
e.g. examples/quickstart/run_agent.py.

Architecture (mirrors nanobot AgentLoop):

    claw  (main LlmAgent)          — orchestrator; receives every user message
      ├─ all standard tools      — exec, filesystem, web, message, skills
      └─ sub_agents: [claw_worker]

    claw_worker  (worker LlmAgent)   — background / long-running task executor
      └─ execution tools only    — exec, filesystem, web

Runtime-injected state (via agent_context metadata, NOT constructor args):
  - MessageTool callback     → MESSAGE_CALLBACK_KEY
  - CronTool channel/chat    → CRON_CHANNEL_KEY, CRON_CHAT_ID_KEY
"""

from __future__ import annotations

from typing import Optional

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.tools import preload_memory_tool
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.types import HttpOptions

from ..config import BOT_NAME
from ..config import ClawConfig
from ..service import CronService
from ..skill import create_skill_tool_set
from ..tools import CronTool
from ..tools import EditFileTool
from ..tools import ExecTool
from ..tools import ListDirTool
from ..tools import MessageTool
from ..tools import ReadFileTool
from ..tools import SpawnTaskTool
from ..tools import WebFetchTool
from ..tools import WebSearchTool
from ..tools import WriteFileTool
from ..tools import build_mcp_toolsets
from ._prompts import ClawPrompts

# ---------------------------------------------------------------------------
# Worker / sub-agent prompt
# ---------------------------------------------------------------------------

_WORKER_INSTRUCTION = """You are a focused background task executor.

Your job is to complete the assigned task step by step using the available tools.
Report your results clearly and concisely when finished.

Guidelines:
- Read files before modifying them.
- Run shell commands carefully; check output before proceeding.
- If an error occurs, analyse it and retry with a corrected approach.
- When the task is done, summarize what you did and what the outcome was.
"""


def create_model(config: ClawConfig) -> LLMModel:
    """Create a model.

    Args:
        config: ClawConfig instance.

    Returns:
        LLMModel instance.
    """
    if not config.model_api_key or not config.model_base_url or not config.model_name:
        raise ValueError("Model config missing. Set runtime.model_* in config or "
                         "TRPC_AGENT_API_KEY/TRPC_AGENT_BASE_URL/TRPC_AGENT_MODEL_NAME.")
    model = OpenAIModel(model_name=config.model_name, api_key=config.model_api_key, base_url=config.model_base_url)
    return model




def create_worker_agent(
    config: ClawConfig,
    model: LLMModel,
    *,
    generate_content_config: Optional[GenerateContentConfig] = None,
) -> LlmAgent:
    """Create the sub-agent(worker / background) agent.

    The worker handles long-running or specialized tasks spawned by the
    parent agent.  It does **not** have access to MessageTool
    to keep its surface small and predictable.

    Args:
        model:                 LLM instance to use.
        config:                ClawConfig instance.
        generate_content_config: GenerateContentConfig instance.

    Returns:
        Configured :class:`LlmAgent` instance.
    """
    allowed_dir = config.workspace if config.tools.restrict_to_workspace else None
    workspace = config.workspace

    return LlmAgent(
        name=f"{BOT_NAME}_worker",
        description=("A background task executor.  Handles long-running or specialized "
                     "tasks delegated by the main claw agent."),
        model=model,
        instruction=_WORKER_INSTRUCTION,
        tools=[
            ReadFileTool(workspace=workspace, allowed_dir=allowed_dir),
            WriteFileTool(workspace=workspace, allowed_dir=allowed_dir),
            EditFileTool(workspace=workspace, allowed_dir=allowed_dir),
            ListDirTool(workspace=workspace, allowed_dir=allowed_dir),
            ExecTool(
                working_dir=str(workspace),
                restrict_to_workspace=config.tools.restrict_to_workspace,
            ),
            WebSearchTool(config=config.tools.web.search, proxy=config.tools.web.proxy),
            WebFetchTool(),
            preload_memory_tool,
        ],
        generate_content_config=generate_content_config,
    )


def create_agent(
    config: ClawConfig,
    model: LLMModel,
    *,
    cron_service: Optional["CronService"] = None,
    worker_agent: Optional[LlmAgent] = None,
) -> LlmAgent:
    """Create the main agent(main / orchestrator) claw agent.

    Wires together all static tools and the system prompt.  Runtime-dynamic
    state (message callbacks, cron channel) is injected via
    ``agent_context`` metadata by the caller — no mutation needed here.

    Args:
        config:                 ClawConfig instance.
        model:                  LLM instance to use.
        cron_service:           nanobot :class:`CronService`; when provided a
                                :class:`CronTool` is added for scheduling.
        worker_agent:           Override the auto-created worker sub-agent.

    Returns:
        Configured :class:`LlmAgent` instance (main agent).
    """
    workspace = config.workspace
    allowed_dir = workspace if config.tools.restrict_to_workspace else None
    if not workspace.exists():
        workspace.mkdir(parents=True, exist_ok=True)

    # ── System prompt ────────────────────────────────────────────────────────
    system_prompt = ClawPrompts(config=config).build_system_prompt()

    generate_content_config = GenerateContentConfig(
        temperature=config.agent.temperature,
        max_output_tokens=config.agent.max_tokens,
        http_options=HttpOptions(headers=config.model_extra_headers, ),
    )
    # ── Sub (worker) agent ───────────────────────────────────────────────────
    child = worker_agent or create_worker_agent(
        config=config,
        model=model,
        generate_content_config=generate_content_config,
    )

    # ── Tools ────────────────────────────────────────────────────────────────
    skill_tool_set = create_skill_tool_set(config)
    tools = [
        # Filesystem
        ReadFileTool(workspace=workspace, allowed_dir=allowed_dir),
        WriteFileTool(workspace=workspace, allowed_dir=allowed_dir),
        EditFileTool(workspace=workspace, allowed_dir=allowed_dir),
        ListDirTool(workspace=workspace, allowed_dir=allowed_dir),
        # Shell execution
        ExecTool(
            working_dir=str(workspace),
            restrict_to_workspace=config.tools.restrict_to_workspace,
        ),
        # Web
        WebSearchTool(config=config.tools.web.search, proxy=config.tools.web.proxy),
        WebFetchTool(),
        # Messaging — send_callback injected at runtime via agent_context
        MessageTool(),
        # Async background task dispatch — callback injected at runtime
        SpawnTaskTool(),
        # Skills
        skill_tool_set,
        preload_memory_tool,
    ]

    # Optional runtime-dependent tools
    tools.extend(build_mcp_toolsets(config.tools.mcp_servers))

    if cron_service is not None:
        tools.append(CronTool(cron_service=cron_service))

    # ── Parent agent ─────────────────────────────────────────────────────────

    return LlmAgent(
        name=BOT_NAME,
        description="A helpful AI assistant powered by claw.",
        model=model,
        instruction=system_prompt,
        tools=tools,
        skill_repository=skill_tool_set.repository,
        generate_content_config=generate_content_config,
        # claw_worker is accessible via transfer_to_agent
        sub_agents=[child],
    )
