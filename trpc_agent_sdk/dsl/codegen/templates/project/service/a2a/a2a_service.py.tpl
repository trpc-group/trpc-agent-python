# -*- coding: utf-8 -*-
#
# Copyright @ 2025 Tencent.com
"""A2A service registration for generated agent service."""

from trpc_a2a.server.a2a_registry import register_service
from trpc_agent_sdk.server.a2a.service import TrpcA2aAgentService


def create_a2a_service(service_name: str) -> TrpcA2aAgentService:
    """Create and initialize A2A agent service."""
    from agent.agent import root_agent

    a2a_service = TrpcA2aAgentService(
        service_name=service_name,
        agent=root_agent,
    )
    a2a_service.initialize()
    return a2a_service


def register_a2a_service():
    """Register A2A service into tRPC runtime."""
    service_name = "{{ service_name }}"
    register_service(service_name, create_a2a_service(service_name))

