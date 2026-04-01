# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Service registry for AG-UI services."""

from typing import Dict
from typing import Optional

from trpc_agent_sdk.utils import SingletonBase

from ._service import AgUiService


class AgUiServiceRegistry(SingletonBase):
    """Registry for AG-UI services."""

    def __init__(self):
        super().__init__()
        self._services: Dict[str, AgUiService] = {}

    def register_service(self, service_name: str, service: AgUiService) -> None:
        """Register an AG-UI service."""
        self._services[service_name] = service

    def get_service(self, service_name: Optional[str] = None) -> Optional[AgUiService]:
        """Get a registered service."""
        if service_name is None:
            return self._services
        return self._services.get(service_name)


_agui_service_registry: Optional[AgUiServiceRegistry] = None


def get_agui_service_registry() -> AgUiServiceRegistry:
    """Get the singleton instance of the AG-UI service registry."""
    global _agui_service_registry
    if _agui_service_registry is None:
        _agui_service_registry = AgUiServiceRegistry()
    return _agui_service_registry
