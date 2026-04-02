# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Extended tests for BaseRegistryFactory."""

import pytest

from trpc_agent_sdk.utils._registry_factory import BaseRegistryFactory


class _DummyCls:
    def __init__(self, val=0):
        self.val = val


class TestBaseRegistryFactory:
    """Test suite for BaseRegistryFactory."""

    def test_register_and_get_cls(self):
        """Test register and get_cls."""
        reg = BaseRegistryFactory()
        reg.register("dummy", _DummyCls)
        assert reg.get_cls("dummy") is _DummyCls

    def test_register_duplicate_raises(self):
        """Test duplicate register raises TypeError."""
        reg = BaseRegistryFactory()
        reg.register("dummy", _DummyCls)
        with pytest.raises(TypeError, match="already registered"):
            reg.register("dummy", _DummyCls)

    def test_get_cls_missing_returns_none(self):
        """Test get_cls for missing returns None."""
        reg = BaseRegistryFactory()
        assert reg.get_cls("nonexistent") is None

    def test_list_cls(self):
        """Test list_cls returns all registered."""
        reg = BaseRegistryFactory()
        reg.register("a", _DummyCls)
        result = reg.list_cls()
        assert "a" in result

    def test_create_instance(self):
        """Test create returns new instance."""
        reg = BaseRegistryFactory()
        reg.register("dummy", _DummyCls)
        instance = reg.create("dummy", val=42)
        assert isinstance(instance, _DummyCls)
        assert instance.val == 42

    def test_create_missing_raises(self):
        """Test create for missing raises KeyError."""
        reg = BaseRegistryFactory()
        with pytest.raises(KeyError, match="No class registered"):
            reg.create("nonexistent")

    def test_create_and_save(self):
        """Test create_and_save creates and stores."""
        reg = BaseRegistryFactory()
        reg.register("dummy", _DummyCls)
        instance = reg.create_and_save("dummy", "obj1", val=10)
        assert reg.get_instance("obj1") is instance

    def test_create_and_save_duplicate_raises(self):
        """Test create_and_save with existing name raises."""
        reg = BaseRegistryFactory()
        reg.register("dummy", _DummyCls)
        reg.create_and_save("dummy", "obj1")
        with pytest.raises(KeyError, match="already exists"):
            reg.create_and_save("dummy", "obj1")

    def test_get_instance_missing(self):
        """Test get_instance for missing returns None."""
        reg = BaseRegistryFactory()
        assert reg.get_instance("nonexistent") is None

    def test_list_instance(self):
        """Test list_instance returns all instances."""
        reg = BaseRegistryFactory()
        reg.register("dummy", _DummyCls)
        reg.create_and_save("dummy", "obj1")
        result = reg.list_instance()
        assert "obj1" in result
