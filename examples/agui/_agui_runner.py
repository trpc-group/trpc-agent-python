# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Runner manager: owns the AG-UI manager for the FastAPI server.
"""
from contextlib import asynccontextmanager
from typing import Any

from ag_ui.core import RunAgentInput
from fastapi import FastAPI
from pydantic import BaseModel

from trpc_agent_sdk.agents import BaseAgent
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.server.ag_ui import AgUiAgent
from trpc_agent_sdk.server.ag_ui import AgUiManager
from trpc_agent_sdk.server.ag_ui import AgUiService
from trpc_agent_sdk.server.ag_ui import AgUiUserFeedBack


def get_agui_user_id(input: RunAgentInput):
    """Extract user_id from input if available."""
    return "user_123"


async def user_feedback_handler(feedback: AgUiUserFeedBack):
    """Handle user feedback in Human-In-The-Loop scenarios."""
    logger.info(f"User Feedback Received:")
    logger.info(f"   Tool: {feedback.tool_name}")
    logger.info(f"   Message: {feedback.tool_message}")


class HealthResponse(BaseModel):
    """Response body for GET /health."""

    status: str = "ok"
    app_name: str
    version: str = "1.0.0"


class AguiRunner:
    """AG-UI runner: owns the AG-UI manager for the FastAPI server."""

    def __init__(
        self,
        app_name: str,
    ) -> None:
        self._app_name = app_name
        self._agui_manager = AgUiManager()
        self._app = self._create_app()

    @property
    def app(self) -> FastAPI:
        """Get the FastAPI app for the AG-UI runner."""
        return self._app

    def register_service(self, service_name: str, service: AgUiService) -> None:
        """Register an AG-UI service.

        Args:
          service_name: The name of the service.
          service: The AG-UI service to register.
        """
        self._agui_manager.register_service(service_name, service)

    def run(self, host: str, port: int, **kwargs: Any) -> None:
        """Run the AG-UI runner.
        
        Args:
            host: The host to run the AG-UI runner on.
            port: The port to run the AG-UI runner on.
            kwargs: Additional keyword arguments to pass to the uvicorn server.
        """
        self._app.get("/health", response_model=HealthResponse, tags=["meta"])(self.health)
        self._agui_manager.set_app(self._app)
        self._agui_manager.run(host, port, **kwargs)

    @asynccontextmanager
    async def _lifespan(self, app: FastAPI):  # noqa: ARG001
        """Startup / shutdown hook: close the runner on exit."""
        logger.info("TRPC AG-UI Server starting up.")
        yield
        logger.info("TRPC AG-UI Server shutting down.")
        await self._agui_manager.close()

    def _create_app(self) -> FastAPI:
        app = FastAPI(
            title="TRPC AG-UI Server",
            description="HTTP API for TRPC AG-UI Server",
            version="1.0.0",
            lifespan=self._lifespan,
        )
        return app

    async def health(self) -> HealthResponse:
        """Liveness check - always returns 200 while the server is up."""
        return HealthResponse(app_name=self._app_name)


def _create_agui_agent(name: str, root_agent: BaseAgent, **kwargs) -> AgUiAgent:
    """Create AgUiAgent in tRPC framework worker process.
    
    Args:
        name: Name of the agent
        root_agent: Root agent instance
    Returns:
        AgUiAgent instance
    """
    agui_agent = AgUiAgent(
        trpc_agent=root_agent,
        app_name=name,
        **kwargs,
        # user_feedback_handler=user_feedback_handler,
        # app_name_extractor=get_agui_user_id,
        # user_id_extractor=xxx,
    )
    return agui_agent


def create_agui_runner(app_name: str, service_name: str, uri: str, **kwargs: Any) -> AguiRunner:
    """Create AgUiService and add agent to it.
    
    Args:
        app_name: Name of the app
        service_name: Name of the service
        uri: URI of the agent
        kwargs: Additional keyword arguments to pass to the AgUiAgent constructor
    Returns:
        AguiRunner instance
    """
    ag_ui_runner: AguiRunner = AguiRunner(app_name)
    agui_service = AgUiService(service_name, app=ag_ui_runner.app)
    agui_agent = _create_agui_agent(app_name, **kwargs)
    agui_service.add_agent(uri, agui_agent)
    ag_ui_runner.register_service(service_name, agui_service)
    return ag_ui_runner
