# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.utils._singleton.

Covers:
- singleton decorator: instance identity, args ignored after first call
- SingletonMeta metaclass: instance identity, _instances dict
- SingletonBase class: inheritance, __init__ called once
"""

from trpc_agent_sdk.utils import SingletonBase, SingletonMeta, singleton


class TestSingletonDecorator:

    def test_same_instance(self):
        @singleton
        class A:
            def __init__(self):
                self.value = 42

        assert A() is A()
        assert A().value == 42

    def test_different_classes(self):
        @singleton
        class X:
            pass

        @singleton
        class Y:
            pass

        assert X() is X()
        assert Y() is Y()
        assert X() is not Y()

    def test_args_ignored_after_first_call(self):
        @singleton
        class C:
            def __init__(self, v):
                self.v = v

        first = C(10)
        second = C(20)
        assert first is second
        assert second.v == 10

    def test_kwargs_ignored_after_first_call(self):
        @singleton
        class D:
            def __init__(self, name="default", age=0):
                self.name = name
                self.age = age

        first = D(name="Alice", age=25)
        second = D(name="Bob", age=30)
        assert first is second
        assert first.name == "Alice"

    def test_many_instantiations(self):
        @singleton
        class E:
            def __init__(self):
                self.counter = 0
                self.counter += 1

        instances = [E() for _ in range(10)]
        for inst in instances:
            assert inst is instances[0]
        assert instances[0].counter == 1

    def test_decorator_replaces_class(self):
        @singleton
        class F:
            pass

        assert callable(F)
        assert not isinstance(F, type)


class TestSingletonMeta:

    def test_same_instance(self):
        class M1(metaclass=SingletonMeta):
            def __init__(self):
                self.value = 100

        assert M1() is M1()
        assert M1().value == 100

    def test_different_classes(self):
        class MA(metaclass=SingletonMeta):
            pass

        class MB(metaclass=SingletonMeta):
            pass

        assert MA() is MA()
        assert MB() is MB()
        assert MA() is not MB()

    def test_args_ignored_after_first(self):
        class M2(metaclass=SingletonMeta):
            def __init__(self, v):
                self.v = v

        assert M2(50) is M2(60)
        assert M2(50).v == 50

    def test_kwargs_ignored_after_first(self):
        class M3(metaclass=SingletonMeta):
            def __init__(self, name="default"):
                self.name = name

        assert M3(name="Test") is M3(name="Another")
        assert M3().name == "Test"

    def test_instances_dict_populated(self):
        class M4(metaclass=SingletonMeta):
            pass

        M4()
        assert M4 in SingletonMeta._instances


class TestSingletonBase:

    def test_same_instance(self):
        class B1(SingletonBase):
            def __init__(self):
                super().__init__()
                self.value = 200

        assert B1() is B1()
        assert B1().value == 200

    def test_different_subclasses(self):
        class BA(SingletonBase):
            def __init__(self):
                super().__init__()
                self.name = "A"

        class BB(SingletonBase):
            def __init__(self):
                super().__init__()
                self.name = "B"

        assert BA() is BA()
        assert BB() is BB()
        assert BA() is not BB()

    def test_init_called_once(self):
        call_count = []

        class B2(SingletonBase):
            def __init__(self):
                super().__init__()
                call_count.append(1)

        for _ in range(5):
            B2()
        assert len(call_count) == 1

    def test_inheritance_chain(self):
        class Base(SingletonBase):
            def __init__(self):
                super().__init__()
                self.base_val = "base"

        class Derived(Base):
            def __init__(self):
                super().__init__()
                self.derived_val = "derived"

        d = Derived()
        assert d is Derived()
        assert d.base_val == "base"
        assert d.derived_val == "derived"

    def test_with_args(self):
        class B3(SingletonBase):
            def __init__(self, v):
                super().__init__()
                self.v = v

        assert B3(300) is B3(400)
        assert B3(300).v == 300

    def test_is_instance_of_base(self):
        class B4(SingletonBase):
            pass

        assert isinstance(B4(), SingletonBase)


class TestSingletonComparison:

    def test_all_three_produce_singletons(self):
        @singleton
        class Dec:
            pass

        class Meta(metaclass=SingletonMeta):
            pass

        class Base(SingletonBase):
            def __init__(self):
                super().__init__()

        assert Dec() is Dec()
        assert Meta() is Meta()
        assert Base() is Base()

    def test_all_three_are_independent(self):
        @singleton
        class Dec2:
            pass

        class Meta2(metaclass=SingletonMeta):
            pass

        class Base2(SingletonBase):
            def __init__(self):
                super().__init__()

        assert Dec2() is not Meta2()
        assert Dec2() is not Base2()
        assert Meta2() is not Base2()
