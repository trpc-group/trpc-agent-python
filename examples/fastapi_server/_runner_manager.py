# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Runner manager: owns the Agent + Runner lifecycle for the FastAPI server.

A single ``RunnerManager`` instance is created at startup and shared across
all HTTP requests.  It holds:

- one ``InMemorySessionService`` (per-process in-memory store),
- one ``Runner`` wired to the configured agent,
- helper utilities for generating session IDs.

Agent loading strategy (in priority order):

1. If ``agent_module`` is provided, import the module and look for
   ``root_agent`` (instance) or ``create_agent()`` (factory).
2. Otherwise build a minimal ``LlmAgent`` backed by the ``OpenAIModel``
   constructed from the supplied CLI / env-var credentials.
"""

import importlib
import uuid
from typing import Optional

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService


class RunnerManager:
    """Owns the agent, runner, and session service for one server process."""

    def __init__(
        self,
        app_name: str,
        model_key: str,
        model_url: str,
        model_name: str,
        agent_module: Optional[str] = None,
        instruction: Optional[str] = None,
    ) -> None:
        self.app_name = app_name
        self._session_service = InMemorySessionService()
        agent = self._load_agent(
            model_key=model_key,
            model_url=model_url,
            model_name=model_name,
            agent_module=agent_module,
            instruction=instruction,
        )
        self._runner = Runner(
            app_name=app_name,
            agent=agent,
            session_service=self._session_service,
        )
        logger.info(
            "RunnerManager started: app=%s agent=%s",
            app_name,
            agent.name,
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def runner(self) -> Runner:
        """The underlying Runner instance."""
        return self._runner

    # ------------------------------------------------------------------
    # Session helpers
    # ------------------------------------------------------------------

    @staticmethod
    def new_session_id() -> str:
        """Generate a fresh UUID-based session ID."""
        return str(uuid.uuid4())

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Gracefully close the runner and release resources."""
        self._runner.close()
        logger.info("RunnerManager closed: app=%s", self.app_name)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_agent(
        self,
        model_key: str,
        model_url: str,
        model_name: str,
        agent_module: Optional[str],
        instruction: Optional[str],
    ) -> LlmAgent:
        """Return a configured LlmAgent, either from a user module or a default."""
        if agent_module:
            return self._load_agent_from_module(agent_module)

        return self._build_default_agent(
            model_key=model_key,
            model_url=model_url,
            model_name=model_name,
            instruction=instruction,
        )

    @staticmethod
    def _load_agent_from_module(module_path: str) -> LlmAgent:
        """Import *module_path* and extract the agent instance or factory."""
        logger.info("Loading agent from module: %s", module_path)
        try:
            mod = importlib.import_module(module_path)
        except ModuleNotFoundError as exc:
            raise ImportError(f"Cannot import agent module {module_path!r}. "
                              "Ensure the module is on PYTHONPATH.") from exc

        if hasattr(mod, "root_agent"):
            agent = mod.root_agent
            logger.info("Using root_agent from %s", module_path)
            return agent

        if hasattr(mod, "create_agent") and callable(mod.create_agent):
            agent = mod.create_agent()
            logger.info("Using create_agent() from %s", module_path)
            return agent

        raise ValueError(f"Module {module_path!r} must export either "
                         "'root_agent' (an LlmAgent instance) or "
                         "'create_agent()' (a zero-argument factory).")

    @staticmethod
    def _build_default_agent(
        model_key: str,
        model_url: str,
        model_name: str,
        instruction: Optional[str],
    ) -> LlmAgent:
        """Create a bare-bones assistant agent from the given credentials."""
        model: LLMModel = OpenAIModel(
            model_name=model_name,
            api_key=model_key,
            base_url=model_url,
        )
        default_instruction = (instruction or "You are a helpful AI assistant. Answer questions clearly and concisely.")
        agent = LlmAgent(
            name="assistant",
            description="A helpful AI assistant.",
            model=model,
            instruction=default_instruction,
        )
        logger.info("Built default agent: model=%s", model_name)
        return agent
