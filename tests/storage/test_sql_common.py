# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Tests for SQL common utilities."""

from __future__ import annotations

import base64
import json
import pickle
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
