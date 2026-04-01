# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import pytest
from trpc_agent_sdk.utils import BaseRegistryFactory


class TestBaseRegistryFactory:
    """Test suite for BaseRegistryFactory class."""

    def test_init(self):
        """Test factory initialization."""
        factory = BaseRegistryFactory()

        assert factory._cls_map == {}
        assert factory._instance_map == {}

    def test_register_class(self):
        """Test registering a class."""

        class TestClass:
            pass

        factory = BaseRegistryFactory()
        result = factory.register("test", TestClass)

        assert result == TestClass
        assert factory.get_cls("test") == TestClass
        assert "test" in factory.list_cls()

    def test_register_duplicate_name(self):
        """Test registering duplicate name raises error."""

        class TestClass1:
            pass

        class TestClass2:
            pass

        factory = BaseRegistryFactory()
        factory.register("test", TestClass1)

        with pytest.raises(TypeError, match="already registered"):
            factory.register("test", TestClass2)

    def test_get_cls_existing(self):
        """Test getting an existing registered class."""

        class TestClass:
            pass

        factory = BaseRegistryFactory()
        factory.register("test", TestClass)

        result = factory.get_cls("test")

        assert result == TestClass

    def test_get_cls_nonexistent(self):
        """Test getting a non-existent registered class."""
        factory = BaseRegistryFactory()

        result = factory.get_cls("nonexistent")

        assert result is None

    def test_get_instance_nonexistent(self):
        """Test getting a non-existent instance."""
        factory = BaseRegistryFactory()

        result = factory.get_instance("nonexistent")

        assert result is None

    def test_list_cls(self):
        """Test listing all registered classes."""

        class ClassA:
            pass

        class ClassB:
            pass

        factory = BaseRegistryFactory()
        factory.register("a", ClassA)
        factory.register("b", ClassB)

        result = factory.list_cls()

        assert len(result) == 2
        assert result["a"] == ClassA
        assert result["b"] == ClassB
        # Verify it's a copy
        assert result is not factory._cls_map

    def test_list_instance(self):
        """Test listing all registered instances."""

        class TestClass:

            def __init__(self, value):
                self.value = value

        factory = BaseRegistryFactory()
        factory.register("test", TestClass)
        instance1 = factory.create_and_save("test", "instance1", value=10)
        instance2 = factory.create_and_save("test", "instance2", value=20)

        result = factory.list_instance()

        assert len(result) == 2
        assert result["instance1"] == instance1
        assert result["instance2"] == instance2
        # Verify it's a copy
        assert result is not factory._instance_map

    def test_create_instance(self):
        """Test creating an instance from registered class."""

        class TestClass:

            def __init__(self, value):
                self.value = value

        factory = BaseRegistryFactory()
        factory.register("test", TestClass)

        instance = factory.create("test", value=42)

        assert isinstance(instance, TestClass)
        assert instance.value == 42

    def test_create_nonexistent_class(self):
        """Test creating instance from non-existent class."""
        factory = BaseRegistryFactory()

        with pytest.raises(KeyError, match="No class registered"):
            factory.create("nonexistent")

    def test_create_and_save(self):
        """Test creating and saving an instance."""

        class TestClass:

            def __init__(self, value):
                self.value = value

        factory = BaseRegistryFactory()
        factory.register("test", TestClass)

        instance = factory.create_and_save("test", "instance1", value=42)

        assert isinstance(instance, TestClass)
        assert instance.value == 42
        assert factory.get_instance("instance1") == instance

    def test_create_and_save_duplicate_name(self):
        """Test creating and saving with duplicate instance name."""

        class TestClass:
            pass

        factory = BaseRegistryFactory()
        factory.register("test", TestClass)
        factory.create_and_save("test", "instance1")

        with pytest.raises(KeyError, match="Instance already exists"):
            factory.create_and_save("test", "instance1")
