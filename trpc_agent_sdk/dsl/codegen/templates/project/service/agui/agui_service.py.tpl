# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""AG-UI service registration for generated agent service."""

from fastapi import Request
from trpc_fastapi import fastapi_route
from trpc_agent_sdk.server.ag_ui import AgUiAgent
from trpc_agent_sdk.server.ag_ui import AgUiService
from trpc_agent_sdk.server.ag_ui import register_service


@fastapi_route("/heath_check", ["GET"], route_params={"response_model": dict})
async def heath_check(request: Request) -> dict:
    del request
    return {"message": "Heath check success!"}


def _create_agui_agent() -> AgUiAgent:
    from agent.agent import root_agent
    return AgUiAgent(
        trpc_agent=root_agent,
        app_name="generated_agui_app",
    )


def register_agui_agent():
    """Register AG-UI agent routes into tRPC runtime."""
    service_name = "{{ service_name }}"
    uri = "{{ agui_uri }}"
    agui_service = AgUiService(service_name)
    agui_service.add_agent(uri, _create_agui_agent)
    register_service(service_name, agui_service)

