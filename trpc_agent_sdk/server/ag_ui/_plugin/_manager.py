# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""AG-UI manager: manages the AG-UI agents and services."""

from typing import Any
from typing import Dict
from typing import Optional

import uvicorn
from fastapi import FastAPI

from .._core import AgUiAgent
from ._registry import get_agui_service_registry
from ._service import AgUiService


class AgUiManager:
    """AG-UI manager: manages the AG-UI agents and services."""

    def __init__(self, app: FastAPI = None):
        self._agui_service_registry = get_agui_service_registry()
        self._agui_agents: Dict[str, AgUiAgent] = {}
        self._app = app

    def set_app(self, app: FastAPI) -> None:
        """Set the FastAPI app.

        Args:
          app: The FastAPI app.
        """
        self._app = app

    def register_service(self, service_name: str, service: AgUiService) -> None:
        """Register an AG-UI service.

        Args:
          service_name: The name of the service.
          service: The AG-UI service to register.
        """
        self._agui_service_registry.register_service(service_name, service)

    def get_service(self, service_name: str) -> Optional[AgUiService]:
        """Get an AG-UI service.

        Args:
          service_name: The name of the service.
        
        Returns:
          The AG-UI service.
        """
        return self._agui_service_registry.get_service(service_name)

    def get_agents(self) -> Dict[str, AgUiAgent]:
        """Get the AG-UI agents.

        Returns:
            A dictionary mapping URI paths to AgUiAgent instances.
        """
        return self._agui_agents

    def _build_agents(self) -> None:
        """Build the AG-UI agents."""
        agui_services: Dict[str, AgUiService] = self._agui_service_registry.get_service()
        for service in agui_services.values():
            service.create_agents()
            self._agui_agents.update(service.agents)
            if service.app is None:
                service.set_fastapi(self._app)

    def run(self, host: str, port: int, **kwargs: Any) -> None:
        """Run the AG-UI manager.

        Args:
         host: The host to run the AG-UI manager on.
         port: The port to run the AG-UI manager on.
         kwargs: Additional keyword arguments to pass to the uvicorn server.
        """
        self._build_agents()
        uvicorn.run(self._app, host=host, port=port, **kwargs)

    async def close(self) -> None:
        """Close the AG-UI manager."""
        for agent in self._agui_agents.values():
            await agent.close()
