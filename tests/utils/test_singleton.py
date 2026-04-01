# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import pytest
from trpc_agent_sdk.utils import SingletonBase
from trpc_agent_sdk.utils import SingletonMeta
from trpc_agent_sdk.utils import singleton


class TestSingletonDecorator:
    """Test suite for singleton decorator function."""

    def test_singleton_decorator_same_instance(self):
        """Test that singleton decorator returns the same instance."""
        @singleton
        class TestClass:
            def __init__(self):
                self.value = 42

        instance1 = TestClass()
        instance2 = TestClass()

        assert instance1 is instance2
        assert instance1.value == 42
        assert instance2.value == 42

    def test_singleton_decorator_different_classes(self):
        """Test that different classes have different singleton instances."""
        @singleton
        class ClassA:
            def __init__(self):
                self.name = "A"

        @singleton
        class ClassB:
            def __init__(self):
                self.name = "B"

        instance_a1 = ClassA()
        instance_a2 = ClassA()
        instance_b1 = ClassB()
        instance_b2 = ClassB()

        assert instance_a1 is instance_a2
        assert instance_b1 is instance_b2
        assert instance_a1 is not instance_b1
        assert instance_a1.name == "A"
        assert instance_b1.name == "B"

    def test_singleton_decorator_with_args(self):
        """Test singleton decorator with constructor arguments."""
        @singleton
        class TestClass:
            def __init__(self, value):
                self.value = value

        # First call creates instance with value=10
        instance1 = TestClass(10)
        # Second call should return same instance, ignoring new args
        instance2 = TestClass(20)

        assert instance1 is instance2
        assert instance1.value == 10  # Should keep original value
        assert instance2.value == 10

    def test_singleton_decorator_with_kwargs(self):
        """Test singleton decorator with keyword arguments."""
        @singleton
        class TestClass:
            def __init__(self, name="default", age=0):
                self.name = name
                self.age = age

        instance1 = TestClass(name="Alice", age=25)
        instance2 = TestClass(name="Bob", age=30)

        assert instance1 is instance2
        assert instance1.name == "Alice"  # Should keep original values
        assert instance1.age == 25

    def test_singleton_decorator_multiple_instantiations(self):
        """Test multiple instantiations return the same instance."""
        @singleton
        class TestClass:
            def __init__(self):
                self.counter = 0
                self.counter += 1

        instances = [TestClass() for _ in range(10)]

        # All should be the same instance
        for instance in instances:
            assert instance is instances[0]
        # Counter should be 1 (__init__ called only once)
        assert instances[0].counter == 1


class TestSingletonMeta:
    """Test suite for SingletonMeta metaclass."""

    def test_singleton_meta_same_instance(self):
        """Test that SingletonMeta returns the same instance."""
        class TestClass(metaclass=SingletonMeta):
            def __init__(self):
                self.value = 100

        instance1 = TestClass()
        instance2 = TestClass()

        assert instance1 is instance2
        assert instance1.value == 100

    def test_singleton_meta_different_classes(self):
        """Test that different classes using SingletonMeta have different instances."""
        class ClassA(metaclass=SingletonMeta):
            def __init__(self):
                self.name = "A"

        class ClassB(metaclass=SingletonMeta):
            def __init__(self):
                self.name = "B"

        instance_a1 = ClassA()
        instance_a2 = ClassA()
        instance_b1 = ClassB()
        instance_b2 = ClassB()

        assert instance_a1 is instance_a2
        assert instance_b1 is instance_b2
        assert instance_a1 is not instance_b1

    def test_singleton_meta_with_args(self):
        """Test SingletonMeta with constructor arguments."""
        class TestClass(metaclass=SingletonMeta):
            def __init__(self, value):
                self.value = value

        instance1 = TestClass(50)
        instance2 = TestClass(60)

        assert instance1 is instance2
        assert instance1.value == 50  # Should keep original value

    def test_singleton_meta_with_kwargs(self):
        """Test SingletonMeta with keyword arguments."""
        class TestClass(metaclass=SingletonMeta):
            def __init__(self, name="default"):
                self.name = name

        instance1 = TestClass(name="Test")
        instance2 = TestClass(name="Another")

        assert instance1 is instance2
        assert instance1.name == "Test"


class TestSingletonBase:
    """Test suite for SingletonBase class."""

    def test_singleton_base_same_instance(self):
        """Test that SingletonBase returns the same instance."""
        class TestClass(SingletonBase):
            def __init__(self):
                super().__init__()
                self.value = 200

        instance1 = TestClass()
        instance2 = TestClass()

        assert instance1 is instance2
        assert instance1.value == 200

    def test_singleton_base_different_classes(self):
        """Test that different classes inheriting from SingletonBase have different instances."""
        class ClassA(SingletonBase):
            def __init__(self):
                super().__init__()
                self.name = "A"

        class ClassB(SingletonBase):
            def __init__(self):
                super().__init__()
                self.name = "B"

        instance_a1 = ClassA()
        instance_a2 = ClassA()
        instance_b1 = ClassB()
        instance_b2 = ClassB()

        assert instance_a1 is instance_a2
        assert instance_b1 is instance_b2
        assert instance_a1 is not instance_b1

    def test_singleton_base_with_args(self):
        """Test SingletonBase with constructor arguments."""
        class TestClass(SingletonBase):
            def __init__(self, value):
                super().__init__()
                self.value = value

        instance1 = TestClass(300)
        instance2 = TestClass(400)

        assert instance1 is instance2
        assert instance1.value == 300  # Should keep original value

    def test_singleton_base_inheritance_chain(self):
        """Test SingletonBase with inheritance chain."""
        class BaseClass(SingletonBase):
            def __init__(self):
                super().__init__()
                self.base_value = "base"

        class DerivedClass(BaseClass):
            def __init__(self):
                super().__init__()
                self.derived_value = "derived"

        instance1 = DerivedClass()
        instance2 = DerivedClass()

        assert instance1 is instance2
        assert instance1.base_value == "base"
        assert instance1.derived_value == "derived"

    def test_singleton_base_init_called_once(self):
        """Test that __init__ is called only once for SingletonBase."""
        call_count = []

        class TestClass(SingletonBase):
            def __init__(self):
                super().__init__()
                call_count.append(1)

        # Create multiple instances
        for _ in range(5):
            TestClass()

        # __init__ should be called only once
        assert len(call_count) == 1


class TestSingletonComparison:
    """Test suite comparing different singleton implementations."""

    def test_decorator_vs_metaclass(self):
        """Test that decorator and metaclass both work correctly."""
        @singleton
        class DecoratorClass:
            def __init__(self):
                self.type = "decorator"

        class MetaClass(metaclass=SingletonMeta):
            def __init__(self):
                self.type = "metaclass"

        class BaseClass(SingletonBase):
            def __init__(self):
                super().__init__()
                self.type = "base"

        dec1 = DecoratorClass()
        dec2 = DecoratorClass()
        meta1 = MetaClass()
        meta2 = MetaClass()
        base1 = BaseClass()
        base2 = BaseClass()

        assert dec1 is dec2
        assert meta1 is meta2
        assert base1 is base2
        assert dec1 is not meta1
        assert dec1 is not base1
        assert meta1 is not base1
