# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for SQL common utilities."""

from __future__ import annotations

import base64
import json
import pickle
from copy import deepcopy
from types import SimpleNamespace
from typing import Iterator
from unittest.mock import MagicMock

import pytest

from sqlalchemy import Text
from sqlalchemy.dialects import mysql
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import DateTime
from sqlalchemy.types import PickleType
from sqlalchemy.types import String

from trpc_agent_sdk.storage._sql_common import (
    DynamicJSON,
    DynamicJSONOptions,
    DynamicPickleType,
    PreciseTimestamp,
    SpannerPickleType,
    StorageData,
    UTF8MB4String,
    decode_content,
    decode_grounding_metadata,
    decode_grounding_metadata,
    GLOBAL_TYPE_DECORATOR_HOOK_REGISTRY,
    TypeDecoratorHookRegistry,
)

# ---------------------------------------------------------------------------
# decode_content
# ---------------------------------------------------------------------------


class TestDecodeContent:

    def test_none_input(self):
        assert decode_content(None) is None

    def test_empty_dict(self):
        assert decode_content({}) is None

    def test_valid_content_dict(self):
        content_dict = {"role": "user", "parts": [{"text": "hello"}]}
        result = decode_content(content_dict)
        assert result is not None
        assert result.role == "user"

    def test_content_with_model_role(self):
        content_dict = {"role": "model", "parts": [{"text": "response"}]}
        result = decode_content(content_dict)
        assert result.role == "model"


# ---------------------------------------------------------------------------
# decode_grounding_metadata
# ---------------------------------------------------------------------------


class TestDecodeGroundingMetadata:

    def test_none_input(self):
        assert decode_grounding_metadata(None) is None

    def test_empty_dict(self):
        assert decode_grounding_metadata({}) is None

    def test_valid_grounding_metadata(self):
        metadata_dict = {"search_entry_point": {"rendered_content": "<b>test</b>"}}
        result = decode_grounding_metadata(metadata_dict)
        assert result is not None


# ---------------------------------------------------------------------------
# DynamicJSONOptions
# ---------------------------------------------------------------------------


class TestDynamicJSONOptions:

    def setup_method(self):
        DynamicJSONOptions._json_dumps_kwargs = {}
        DynamicJSONOptions._json_loads_kwargs = {}

    def test_default_dumps_kwargs_empty(self):
        assert DynamicJSONOptions.get_json_dumps_kwargs() == {}

    def test_default_loads_kwargs_empty(self):
        assert DynamicJSONOptions.get_json_loads_kwargs() == {}

    def test_set_dumps_kwargs(self):
        DynamicJSONOptions.set_json_dumps_kwargs({"ensure_ascii": False})
        result = DynamicJSONOptions.get_json_dumps_kwargs()
        assert result == {"ensure_ascii": False}

    def test_set_loads_kwargs(self):
        DynamicJSONOptions.set_json_loads_kwargs({"strict": False})
        result = DynamicJSONOptions.get_json_loads_kwargs()
        assert result == {"strict": False}

    def test_set_dumps_kwargs_updates(self):
        DynamicJSONOptions.set_json_dumps_kwargs({"ensure_ascii": False})
        DynamicJSONOptions.set_json_dumps_kwargs({"indent": 2})
        result = DynamicJSONOptions.get_json_dumps_kwargs()
        assert result == {"ensure_ascii": False, "indent": 2}

    def test_set_loads_kwargs_updates(self):
        DynamicJSONOptions.set_json_loads_kwargs({"strict": False})
        DynamicJSONOptions.set_json_loads_kwargs({"encoding": "utf-8"})
        result = DynamicJSONOptions.get_json_loads_kwargs()
        assert result == {"strict": False, "encoding": "utf-8"}

    def test_set_dumps_kwargs_overwrites_existing_key(self):
        DynamicJSONOptions.set_json_dumps_kwargs({"ensure_ascii": False})
        DynamicJSONOptions.set_json_dumps_kwargs({"ensure_ascii": True})
        result = DynamicJSONOptions.get_json_dumps_kwargs()
        assert result["ensure_ascii"] is True


# ---------------------------------------------------------------------------
# DynamicJSON TypeDecorator
# ---------------------------------------------------------------------------


def _make_dialect(name: str) -> MagicMock:
    """Helper to create a mock SQLAlchemy dialect."""
    dialect = MagicMock()
    dialect.name = name
    if name == "postgresql":
        dialect.type_descriptor = lambda t: t
    elif name == "mysql":
        dialect.type_descriptor = lambda t: t
    else:
        dialect.type_descriptor = lambda t: t
    return dialect


class TestDynamicJSON:

    def test_impl_is_text(self):
        assert DynamicJSON.impl is Text

    def test_load_dialect_impl_postgresql(self):
        dj = DynamicJSON()
        dialect = _make_dialect("postgresql")
        result = dj.load_dialect_impl(dialect)
        assert result is postgresql.JSONB or isinstance(result, postgresql.JSONB)

    def test_load_dialect_impl_mysql(self):
        dj = DynamicJSON()
        dialect = _make_dialect("mysql")
        result = dj.load_dialect_impl(dialect)
        assert result is mysql.LONGTEXT or isinstance(result, mysql.LONGTEXT)

    def test_load_dialect_impl_sqlite(self):
        dj = DynamicJSON()
        dialect = _make_dialect("sqlite")
        result = dj.load_dialect_impl(dialect)
        assert result is Text or isinstance(result, Text)

    def test_process_bind_param_postgresql_returns_dict(self):
        dj = DynamicJSON()
        dialect = _make_dialect("postgresql")
        value = {"key": "value"}
        result = dj.process_bind_param(value, dialect)
        assert result == {"key": "value"}

    def test_process_bind_param_sqlite_returns_json_string(self):
        dj = DynamicJSON()
        dialect = _make_dialect("sqlite")
        value = {"key": "value", "num": 42}
        result = dj.process_bind_param(value, dialect)
        assert isinstance(result, str)
        assert json.loads(result) == value

    def test_process_bind_param_mysql_returns_json_string(self):
        dj = DynamicJSON()
        dialect = _make_dialect("mysql")
        value = {"items": [1, 2, 3]}
        result = dj.process_bind_param(value, dialect)
        assert isinstance(result, str)
        assert json.loads(result) == value

    def test_process_bind_param_none(self):
        dj = DynamicJSON()
        dialect = _make_dialect("sqlite")
        assert dj.process_bind_param(None, dialect) is None

    def test_process_result_value_postgresql_returns_dict(self):
        dj = DynamicJSON()
        dialect = _make_dialect("postgresql")
        value = {"key": "value"}
        result = dj.process_result_value(value, dialect)
        assert result == {"key": "value"}

    def test_process_result_value_sqlite_parses_json(self):
        dj = DynamicJSON()
        dialect = _make_dialect("sqlite")
        value = '{"key": "value", "num": 42}'
        result = dj.process_result_value(value, dialect)
        assert result == {"key": "value", "num": 42}

    def test_process_result_value_none(self):
        dj = DynamicJSON()
        dialect = _make_dialect("sqlite")
        assert dj.process_result_value(None, dialect) is None

    def test_process_bind_param_respects_dumps_kwargs(self):
        DynamicJSONOptions._json_dumps_kwargs = {}
        DynamicJSONOptions.set_json_dumps_kwargs({"ensure_ascii": False})

        dj = DynamicJSON()
        dialect = _make_dialect("sqlite")
        value = {"text": "你好"}
        result = dj.process_bind_param(value, dialect)
        assert "你好" in result

        DynamicJSONOptions._json_dumps_kwargs = {}


# ---------------------------------------------------------------------------
# UTF8MB4String TypeDecorator
# ---------------------------------------------------------------------------


class TestUTF8MB4String:

    def test_impl_is_string(self):
        assert UTF8MB4String.impl is String

    def test_cache_ok(self):
        assert UTF8MB4String.cache_ok is True

    def test_init_with_length(self):
        s = UTF8MB4String(length=255)
        assert s.length == 255

    def test_init_without_length(self):
        s = UTF8MB4String()
        assert s.length is None

    def test_load_dialect_impl_mysql(self):
        s = UTF8MB4String(length=128)
        dialect = _make_dialect("mysql")
        result = s.load_dialect_impl(dialect)
        assert isinstance(result, mysql.VARCHAR)

    def test_load_dialect_impl_sqlite_with_length(self):
        s = UTF8MB4String(length=128)
        dialect = _make_dialect("sqlite")
        result = s.load_dialect_impl(dialect)
        assert isinstance(result, String)

    def test_load_dialect_impl_sqlite_without_length(self):
        s = UTF8MB4String()
        dialect = _make_dialect("sqlite")
        result = s.load_dialect_impl(dialect)
        assert isinstance(result, String)

    def test_load_dialect_impl_postgresql(self):
        s = UTF8MB4String(length=256)
        dialect = _make_dialect("postgresql")
        result = s.load_dialect_impl(dialect)
        assert isinstance(result, String)


# ---------------------------------------------------------------------------
# PreciseTimestamp TypeDecorator
# ---------------------------------------------------------------------------


class TestPreciseTimestamp:

    def test_impl_is_datetime(self):
        assert PreciseTimestamp.impl is DateTime

    def test_cache_ok(self):
        assert PreciseTimestamp.cache_ok is True

    def test_load_dialect_impl_mysql(self):
        pt = PreciseTimestamp()
        dialect = _make_dialect("mysql")
        result = pt.load_dialect_impl(dialect)
        assert isinstance(result, mysql.DATETIME)

    def test_load_dialect_impl_sqlite(self):
        pt = PreciseTimestamp()
        dialect = _make_dialect("sqlite")
        result = pt.load_dialect_impl(dialect)
        assert result is DateTime or isinstance(result, DateTime)


# ---------------------------------------------------------------------------
# DynamicPickleType TypeDecorator
# ---------------------------------------------------------------------------


class TestDynamicPickleType:

    def test_impl_is_pickle_type(self):
        assert DynamicPickleType.impl is PickleType

    def test_load_dialect_impl_spanner(self):
        dpt = DynamicPickleType()
        dialect = _make_dialect("spanner+spanner")
        result = dpt.load_dialect_impl(dialect)
        assert result is SpannerPickleType

    def test_load_dialect_impl_non_spanner(self):
        dpt = DynamicPickleType()
        dialect = _make_dialect("sqlite")
        result = dpt.load_dialect_impl(dialect)
        assert result is PickleType or isinstance(result, PickleType)

    def test_process_bind_param_spanner(self):
        dpt = DynamicPickleType()
        dialect = _make_dialect("spanner+spanner")
        value = {"key": "value", "nums": [1, 2, 3]}
        result = dpt.process_bind_param(value, dialect)
        assert pickle.loads(result) == value

    def test_process_bind_param_non_spanner(self):
        dpt = DynamicPickleType()
        dialect = _make_dialect("sqlite")
        value = {"key": "value"}
        result = dpt.process_bind_param(value, dialect)
        assert result == value

    def test_process_bind_param_none(self):
        dpt = DynamicPickleType()
        dialect = _make_dialect("spanner+spanner")
        assert dpt.process_bind_param(None, dialect) is None

    def test_process_result_value_spanner(self):
        dpt = DynamicPickleType()
        dialect = _make_dialect("spanner+spanner")
        original = {"key": "value", "nums": [1, 2, 3]}
        pickled = pickle.dumps(original)
        result = dpt.process_result_value(pickled, dialect)
        assert result == original

    def test_process_result_value_non_spanner(self):
        dpt = DynamicPickleType()
        dialect = _make_dialect("sqlite")
        value = {"key": "value"}
        result = dpt.process_result_value(value, dialect)
        assert result == value

    def test_process_result_value_none(self):
        dpt = DynamicPickleType()
        dialect = _make_dialect("spanner+spanner")
        assert dpt.process_result_value(None, dialect) is None


# ---------------------------------------------------------------------------
# SpannerPickleType TypeDecorator
# ---------------------------------------------------------------------------


class TestSpannerPickleType:

    def test_impl_is_pickle_type(self):
        assert SpannerPickleType.impl is PickleType

    def test_bind_processor_encodes_base64(self):
        spt = SpannerPickleType()
        dialect = _make_dialect("spanner+spanner")
        processor = spt.bind_processor(dialect)

        raw = b"some pickled data"
        result = processor(raw)
        assert result == base64.standard_b64encode(raw)

    def test_bind_processor_none(self):
        spt = SpannerPickleType()
        dialect = _make_dialect("spanner+spanner")
        processor = spt.bind_processor(dialect)
        assert processor(None) is None

    def test_result_processor_decodes_base64(self):
        spt = SpannerPickleType()
        dialect = _make_dialect("spanner+spanner")
        processor = spt.result_processor(dialect, None)

        raw = b"some pickled data"
        encoded = base64.standard_b64encode(raw)
        result = processor(encoded)
        assert result == raw

    def test_result_processor_none(self):
        spt = SpannerPickleType()
        dialect = _make_dialect("spanner+spanner")
        processor = spt.result_processor(dialect, None)
        assert processor(None) is None

    def test_roundtrip_bind_result(self):
        spt = SpannerPickleType()
        dialect = _make_dialect("spanner+spanner")
        bind = spt.bind_processor(dialect)
        result = spt.result_processor(dialect, None)

        original = pickle.dumps({"hello": "world"})
        encoded = bind(original)
        decoded = result(encoded)
        assert decoded == original
        assert pickle.loads(decoded) == {"hello": "world"}


# ---------------------------------------------------------------------------
# StorageData DeclarativeBase
# ---------------------------------------------------------------------------


class TestStorageData:

    def test_is_declarative_base(self):
        assert issubclass(StorageData, DeclarativeBase)

    def test_has_metadata(self):
        assert StorageData.metadata is not None

    def test_has_registry(self):
        assert StorageData.registry is not None


# ---------------------------------------------------------------------------
# Package re-exports
# ---------------------------------------------------------------------------


class TestSqlCommonReexports:

    def test_all_symbols_reexported(self):
        from trpc_agent_sdk.storage import (
            DynamicJSON as _DJ,
            DynamicJSONOptions as _DJO,
            DynamicPickleType as _DPT,
            PreciseTimestamp as _PT,
            SpannerPickleType as _SPT,
            StorageData as _SD,
            UTF8MB4String as _U,
            decode_content as _dc,
            decode_grounding_metadata as _dg,
        )

        assert _DJ is DynamicJSON
        assert _DJO is DynamicJSONOptions
        assert _DPT is DynamicPickleType
        assert _PT is PreciseTimestamp
        assert _SPT is SpannerPickleType
        assert _SD is StorageData
        assert _U is UTF8MB4String
        assert _dc is decode_content
        assert _dg is decode_grounding_metadata


def _build_dialect(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name, type_descriptor=lambda t: t)


@pytest.fixture(autouse=True)
def reset_hook_registry() -> Iterator[None]:
    """Reset global hook registry around each test."""
    old_load = deepcopy(TypeDecoratorHookRegistry._load_dialect_hooks)
    old_bind = deepcopy(TypeDecoratorHookRegistry._process_bind_hooks)
    old_result = deepcopy(TypeDecoratorHookRegistry._process_result_hooks)
    try:
        TypeDecoratorHookRegistry._load_dialect_hooks = {}
        TypeDecoratorHookRegistry._process_bind_hooks = {}
        TypeDecoratorHookRegistry._process_result_hooks = {}
        yield
    finally:
        TypeDecoratorHookRegistry._load_dialect_hooks = old_load
        TypeDecoratorHookRegistry._process_bind_hooks = old_bind
        TypeDecoratorHookRegistry._process_result_hooks = old_result


def test_dynamic_json_all_hooks_can_override() -> None:
    """DynamicJSON supports load/bind/result hook overrides."""
    json_type = DynamicJSON()
    sqlite = _build_dialect("sqlite")
    load_marker = object()

    def load_hook(decorator, dialect):  # noqa: ANN001
        assert decorator is json_type
        assert dialect.name == "sqlite"
        return load_marker

    def bind_hook(decorator, value, dialect):  # noqa: ANN001
        assert decorator is json_type
        assert dialect.name == "sqlite"
        return f"hooked-bind-{value}"

    def result_hook(decorator, value, dialect):  # noqa: ANN001
        assert decorator is json_type
        assert dialect.name == "sqlite"
        return {"hooked_result": value}

    GLOBAL_TYPE_DECORATOR_HOOK_REGISTRY.register_load_dialect_hook(DynamicJSON, load_hook)
    GLOBAL_TYPE_DECORATOR_HOOK_REGISTRY.register_process_bind_hook(DynamicJSON, bind_hook)
    GLOBAL_TYPE_DECORATOR_HOOK_REGISTRY.register_process_result_hook(DynamicJSON, result_hook)

    assert json_type.load_dialect_impl(sqlite) is load_marker
    assert json_type.process_bind_param({"k": "v"}, sqlite) == "hooked-bind-{'k': 'v'}"
    assert json_type.process_result_value('{"k":"v"}', sqlite) == {"hooked_result": '{"k":"v"}'}


def test_dynamic_json_hook_none_falls_back_to_default_logic() -> None:
    """Hook skips when returning None."""
    json_type = DynamicJSON()
    sqlite = _build_dialect("sqlite")

    GLOBAL_TYPE_DECORATOR_HOOK_REGISTRY.register_process_bind_hook(DynamicJSON, lambda _d, _v, _dialect: None)
    GLOBAL_TYPE_DECORATOR_HOOK_REGISTRY.register_process_result_hook(DynamicJSON, lambda _d, _v, _dialect: None)

    encoded = json_type.process_bind_param({"a": 1}, sqlite)
    decoded = json_type.process_result_value(encoded, sqlite)

    assert encoded == '{"a": 1}'
    assert decoded == {"a": 1}


def test_precise_timestamp_supports_all_three_hooks() -> None:
    """PreciseTimestamp supports load/bind/result hooks."""
    ts_type = PreciseTimestamp()
    sqlite = _build_dialect("sqlite")
    load_marker = object()

    GLOBAL_TYPE_DECORATOR_HOOK_REGISTRY.register_load_dialect_hook(PreciseTimestamp, lambda _d, _dialect: load_marker)
    GLOBAL_TYPE_DECORATOR_HOOK_REGISTRY.register_process_bind_hook(PreciseTimestamp,
                                                                   lambda _d, value, _dialect: f"bind-{value}")
    GLOBAL_TYPE_DECORATOR_HOOK_REGISTRY.register_process_result_hook(PreciseTimestamp,
                                                                     lambda _d, value, _dialect: f"result-{value}")

    assert ts_type.load_dialect_impl(sqlite) is load_marker
    assert ts_type.process_bind_param("2026-01-01", sqlite) == "bind-2026-01-01"
    assert ts_type.process_result_value("2026-01-01", sqlite) == "result-2026-01-01"


def test_utf8mb4_string_supports_all_three_hooks() -> None:
    """UTF8MB4String supports load/bind/result hooks."""
    str_type = UTF8MB4String(length=128)
    sqlite = _build_dialect("sqlite")
    load_marker = object()

    GLOBAL_TYPE_DECORATOR_HOOK_REGISTRY.register_load_dialect_hook(UTF8MB4String, lambda _d, _dialect: load_marker)
    GLOBAL_TYPE_DECORATOR_HOOK_REGISTRY.register_process_bind_hook(UTF8MB4String,
                                                                   lambda _d, value, _dialect: f"bind-{value}")
    GLOBAL_TYPE_DECORATOR_HOOK_REGISTRY.register_process_result_hook(UTF8MB4String,
                                                                     lambda _d, value, _dialect: f"result-{value}")

    assert str_type.load_dialect_impl(sqlite) is load_marker
    assert str_type.process_bind_param("hello", sqlite) == "bind-hello"
    assert str_type.process_result_value("hello", sqlite) == "result-hello"


def test_dynamic_pickle_hook_order_uses_first_override_result() -> None:
    """First non-None hook result wins for DynamicPickleType."""
    pickle_type = DynamicPickleType()
    sqlite = _build_dialect("sqlite")
    calls: list[str] = []

    def hook_a(_d, _v, _dialect):  # noqa: ANN001
        calls.append("a")
        return None

    def hook_b(_d, _v, _dialect):  # noqa: ANN001
        calls.append("b")
        return "override"

    def hook_c(_d, _v, _dialect):  # noqa: ANN001
        calls.append("c")
        return "should-not-run"

    GLOBAL_TYPE_DECORATOR_HOOK_REGISTRY.register_process_bind_hook(DynamicPickleType, hook_a)
    GLOBAL_TYPE_DECORATOR_HOOK_REGISTRY.register_process_bind_hook(DynamicPickleType, hook_b)
    GLOBAL_TYPE_DECORATOR_HOOK_REGISTRY.register_process_bind_hook(DynamicPickleType, hook_c)

    assert pickle_type.process_bind_param({"k": "v"}, sqlite) == "override"
    assert calls == ["a", "b"]
