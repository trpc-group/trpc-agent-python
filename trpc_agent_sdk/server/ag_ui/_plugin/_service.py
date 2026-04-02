# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Service for managing AG-UI agents."""

from typing import Callable
from typing import Dict
from typing import Optional
from typing import Union

from ag_ui.core import RunAgentInput
from ag_ui.encoder import EventEncoder
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Request
from fastapi.responses import StreamingResponse
from trpc_agent_sdk.log import logger

from .._core import AgUiAgent
from ._utils import event_generator


class AgUiService:
    """Service for managing AG-UI agents.

    This service provides functionality to register and manage multiple AG-UI agents
    with their associated URI endpoints. It handles agent registration and FastAPI
    route configuration.
    """

    def __init__(self, service_name: str, app: Optional[FastAPI] = None, agents: Dict[str, AgUiAgent] = None):
        """Initialize the AgUiService.

        Args:
            service_name: Name of the service used for route registration
            app: Optional FastAPI app instance. If not provided, the service will not be registered with the FastAPI app.
            agents: Optional dictionary of agents keyed by URI path. If not provided,
                   an empty dictionary will be used.
        """
        self._service_name = service_name
        self._app = app
        self._agents: Dict[str, AgUiAgent] = agents or {}
        self._agui_agent_factories: Dict[str, Callable[[], AgUiAgent]] = {}

    @property
    def app(self) -> FastAPI:
        """Get the FastAPI app for the service.

        Returns:
            The FastAPI app instance.
        """
        return self._app

    @property
    def service_name(self) -> str:
        """Get the name of the service.

        Returns:
            The service name string.
        """
        return self._service_name

    @property
    def agents(self) -> Dict[str, AgUiAgent]:
        """Get the agents of the service.

        Returns:
            A dictionary mapping URI paths to AgUiAgent instances.
        """
        return self._agents

    def create_agents(self) -> None:
        """Create AgUiAgent instances.

        This is a base implementation that returns an empty dictionary.
        Subclasses should override this method to provide actual agent creation logic.

        Returns:
            A dictionary mapping URI paths to AgUiAgent instances. The base
            implementation returns an empty dictionary.
        """
        if not self._agui_agent_factories:
            return None
        for uri, factory in self._agui_agent_factories.items():
            self._agents[uri] = factory()

    def add_agent(self, uri: str, agui_agent: Union[AgUiAgent, Callable[[], AgUiAgent]]) -> None:
        """Add an AgUiAgent with a specific URI.

        This method registers an agent instance directly with the service and sets up
        the FastAPI route for handling requests. Note that this method is primarily
        for backward compatibility with older interfaces and is not recommended for
        use, especially in multi-process environments where agents may have cross-process
        risks.

        Args:
            uri: The URI path for the agent endpoint
            agui_agent: The AgUiAgent instance to register
        """
        if isinstance(agui_agent, Callable):
            self._agui_agent_factories[uri] = agui_agent
        else:
            self._agents[uri] = agui_agent
        self._app.add_api_route(uri, self._ag_ui_agent_endpoint, methods=["POST"], response_model=None)

    def set_fastapi(self, app: FastAPI):
        """Set the FastAPI app for the service.

        This method allows setting a custom FastAPI application instance for the service.
        It is recommended to avoid using this method unless necessary, as it may cause
        issues in multi-process environments.

        Args:
            app: The FastAPI application instance to register with the service

        Raises:
            ValueError: If the app is not a FastAPI instance

        Note:
            Example usage for adding custom routes:

            ```python
            from fastapi import Request
            from trpc_fastapi import fastapi_route

            @fastapi_route("/health_check", ["GET"], route_params={'response_model': dict})
            async def health_check(request: Request) -> dict:
                return {"message": "Health check success!"}
            ```
        """
        self._app = app

    async def _ag_ui_agent_endpoint(self, input_data: RunAgentInput, request: Request):
        """AG-UI agent endpoint with tRPC context and filters."""
        # Get the accept header from the request
        accept_header = request.headers.get("accept")
        logger.info("accept_header: %s", request.url.path)

        # Create an event encoder to properly format SSE events
        encoder = EventEncoder(accept=accept_header)
        if request.url.path not in self._agents:
            raise HTTPException(status_code=404, detail=f"Agent not found for path: {request.url.path}")
        agui_agent: AgUiAgent = self._agents[request.url.path]

        return StreamingResponse(event_generator(request, agui_agent, input_data, encoder),
                                 media_type=encoder.get_content_type())
