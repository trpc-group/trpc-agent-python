# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for trpc_agent_sdk.utils._registry_factory.

Covers:
- BaseRegistryFactory: init, register, get_cls, get_instance, list_cls,
  list_instance, create, create_and_save, error paths, copy semantics
"""

import pytest

from trpc_agent_sdk.utils import BaseRegistryFactory


class _SampleA:
    def __init__(self, value=0):
        self.value = value


class _SampleB:
    pass


class TestBaseRegistryFactoryInit:

    def test_empty_maps(self):
        factory = BaseRegistryFactory()
        assert factory._cls_map == {}
        assert factory._instance_map == {}


class TestBaseRegistryFactoryRegister:

    def test_register_returns_class(self):
        factory = BaseRegistryFactory()
        result = factory.register("a", _SampleA)
        assert result is _SampleA

    def test_register_stores_in_cls_map(self):
        factory = BaseRegistryFactory()
        factory.register("a", _SampleA)
        assert factory.get_cls("a") is _SampleA

    def test_register_multiple(self):
        factory = BaseRegistryFactory()
        factory.register("a", _SampleA)
        factory.register("b", _SampleB)
        assert len(factory.list_cls()) == 2

    def test_register_duplicate_raises_type_error(self):
        factory = BaseRegistryFactory()
        factory.register("a", _SampleA)
        with pytest.raises(TypeError, match="already registered"):
            factory.register("a", _SampleB)


class TestBaseRegistryFactoryGetCls:

    def test_existing(self):
        factory = BaseRegistryFactory()
        factory.register("a", _SampleA)
        assert factory.get_cls("a") is _SampleA

    def test_nonexistent_returns_none(self):
        factory = BaseRegistryFactory()
        assert factory.get_cls("missing") is None


class TestBaseRegistryFactoryGetInstance:

    def test_nonexistent_returns_none(self):
        factory = BaseRegistryFactory()
        assert factory.get_instance("missing") is None

    def test_after_create_and_save(self):
        factory = BaseRegistryFactory()
        factory.register("a", _SampleA)
        inst = factory.create_and_save("a", "inst1", value=10)
        assert factory.get_instance("inst1") is inst


class TestBaseRegistryFactoryListCls:

    def test_returns_copy(self):
        factory = BaseRegistryFactory()
        factory.register("a", _SampleA)
        result = factory.list_cls()
        assert result is not factory._cls_map
        assert result == {"a": _SampleA}

    def test_mutating_copy_does_not_affect_original(self):
        factory = BaseRegistryFactory()
        factory.register("a", _SampleA)
        result = factory.list_cls()
        result["x"] = _SampleB
        assert "x" not in factory._cls_map


class TestBaseRegistryFactoryListInstance:

    def test_returns_copy(self):
        factory = BaseRegistryFactory()
        factory.register("a", _SampleA)
        factory.create_and_save("a", "inst1")
        result = factory.list_instance()
        assert result is not factory._instance_map
        assert "inst1" in result

    def test_empty(self):
        factory = BaseRegistryFactory()
        assert factory.list_instance() == {}


class TestBaseRegistryFactoryCreate:

    def test_creates_instance(self):
        factory = BaseRegistryFactory()
        factory.register("a", _SampleA)
        inst = factory.create("a", value=42)
        assert isinstance(inst, _SampleA)
        assert inst.value == 42

    def test_creates_new_each_time(self):
        factory = BaseRegistryFactory()
        factory.register("a", _SampleA)
        inst1 = factory.create("a")
        inst2 = factory.create("a")
        assert inst1 is not inst2

    def test_nonexistent_raises_key_error(self):
        factory = BaseRegistryFactory()
        with pytest.raises(KeyError, match="No class registered"):
            factory.create("missing")

    def test_with_positional_args(self):
        factory = BaseRegistryFactory()
        factory.register("a", _SampleA)
        inst = factory.create("a", 99)
        assert inst.value == 99


class TestBaseRegistryFactoryCreateAndSave:

    def test_creates_and_stores(self):
        factory = BaseRegistryFactory()
        factory.register("a", _SampleA)
        inst = factory.create_and_save("a", "inst1", value=42)
        assert isinstance(inst, _SampleA)
        assert inst.value == 42
        assert factory.get_instance("inst1") is inst

    def test_duplicate_instance_name_raises_key_error(self):
        factory = BaseRegistryFactory()
        factory.register("a", _SampleA)
        factory.create_and_save("a", "inst1")
        with pytest.raises(KeyError, match="Instance already exists"):
            factory.create_and_save("a", "inst1")

    def test_nonexistent_cls_type_raises_key_error(self):
        factory = BaseRegistryFactory()
        with pytest.raises(KeyError, match="No class registered"):
            factory.create_and_save("missing", "inst1")

    def test_multiple_instances_different_names(self):
        factory = BaseRegistryFactory()
        factory.register("a", _SampleA)
        inst1 = factory.create_and_save("a", "inst1", value=1)
        inst2 = factory.create_and_save("a", "inst2", value=2)
        assert inst1 is not inst2
        assert factory.get_instance("inst1").value == 1
        assert factory.get_instance("inst2").value == 2

    def test_different_cls_types_same_obj_name(self):
        factory = BaseRegistryFactory()
        factory.register("a", _SampleA)
        factory.create_and_save("a", "shared_name")
        with pytest.raises(KeyError, match="Instance already exists"):
            factory.create_and_save("a", "shared_name")
