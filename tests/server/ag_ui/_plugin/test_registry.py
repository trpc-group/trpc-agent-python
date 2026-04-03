# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for AgUiServiceRegistry and get_agui_service_registry."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from trpc_agent_sdk.server.ag_ui._plugin._registry import (
    AgUiServiceRegistry,
    get_agui_service_registry,
)
from trpc_agent_sdk.server.ag_ui._plugin._service import AgUiService
from trpc_agent_sdk.utils._singleton import SingletonMeta


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the AgUiServiceRegistry singleton and module-level variable between tests."""
    SingletonMeta._instances.pop(AgUiServiceRegistry, None)
    with patch(
        "trpc_agent_sdk.server.ag_ui._plugin._registry._agui_service_registry",
        None,
    ):
        yield
    SingletonMeta._instances.pop(AgUiServiceRegistry, None)


class TestAgUiServiceRegistry:
    def test_register_and_get_service(self):
        registry = AgUiServiceRegistry()
        service = Mock(spec=AgUiService)
        registry.register_service("svc1", service)

        assert registry.get_service("svc1") is service

    def test_get_service_unknown_returns_none(self):
        registry = AgUiServiceRegistry()
        assert registry.get_service("nonexistent") is None

    def test_get_service_none_returns_all_services(self):
        registry = AgUiServiceRegistry()
        svc_a = Mock(spec=AgUiService)
        svc_b = Mock(spec=AgUiService)
        registry.register_service("a", svc_a)
        registry.register_service("b", svc_b)

        result = registry.get_service(None)
        assert isinstance(result, dict)
        assert result == {"a": svc_a, "b": svc_b}

    def test_register_overwrites_existing(self):
        registry = AgUiServiceRegistry()
        old = Mock(spec=AgUiService)
        new = Mock(spec=AgUiService)
        registry.register_service("svc", old)
        registry.register_service("svc", new)

        assert registry.get_service("svc") is new

    def test_empty_registry_returns_empty_dict_for_none(self):
        registry = AgUiServiceRegistry()
        assert registry.get_service(None) == {}


class TestGetAguiServiceRegistry:
    def test_returns_instance(self):
        registry = get_agui_service_registry()
        assert isinstance(registry, AgUiServiceRegistry)

    def test_returns_same_instance_on_repeated_calls(self):
        r1 = get_agui_service_registry()
        r2 = get_agui_service_registry()
        assert r1 is r2

    def test_creates_new_instance_after_reset(self):
        r1 = get_agui_service_registry()
        # Reset the module-level var AND the singleton metaclass
        SingletonMeta._instances.pop(AgUiServiceRegistry, None)
        with patch(
            "trpc_agent_sdk.server.ag_ui._plugin._registry._agui_service_registry",
            None,
        ):
            r2 = get_agui_service_registry()
            assert r2 is not r1
