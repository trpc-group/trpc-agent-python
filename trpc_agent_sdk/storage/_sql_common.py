# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
#
# Below code are copy and modified from https://github.com/google/adk-python.git
#
# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""SQL common utilities for TRPC Agent framework."""

from __future__ import annotations

import base64
import json
import pickle
from typing import Any
from typing import Callable
from typing import Optional

from sqlalchemy import Dialect
from sqlalchemy import Text
from sqlalchemy.dialects import mysql
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import DateTime
from sqlalchemy.types import PickleType
from sqlalchemy.types import String
from sqlalchemy.types import TypeDecorator

from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import GroundingMetadata
from trpc_agent_sdk.types import GenerateContentResponseUsageMetadata

LoadDialectHook = Callable[[TypeDecorator, Dialect], Any]
ProcessBindHook = Callable[[TypeDecorator, Any, Dialect], Any]
ProcessResultHook = Callable[[TypeDecorator, Any, Dialect], Any]


class TypeDecoratorHookRegistry:
    """Global hook registry for SQLAlchemy TypeDecorator callbacks."""

    _load_dialect_hooks: dict[type[TypeDecorator], list[LoadDialectHook]] = {}
    _process_bind_hooks: dict[type[TypeDecorator], list[ProcessBindHook]] = {}
    _process_result_hooks: dict[type[TypeDecorator], list[ProcessResultHook]] = {}

    @classmethod
    def register_load_dialect_hook(cls, decorator_cls: type[TypeDecorator], hook: LoadDialectHook) -> None:
        """Register hook for ``load_dialect_impl``."""
        cls._load_dialect_hooks.setdefault(decorator_cls, []).append(hook)

    @classmethod
    def register_process_bind_hook(cls, decorator_cls: type[TypeDecorator], hook: ProcessBindHook) -> None:
        """Register hook for ``process_bind_param``."""
        cls._process_bind_hooks.setdefault(decorator_cls, []).append(hook)

    @classmethod
    def register_process_result_hook(cls, decorator_cls: type[TypeDecorator], hook: ProcessResultHook) -> None:
        """Register hook for ``process_result_value``."""
        cls._process_result_hooks.setdefault(decorator_cls, []).append(hook)

    @classmethod
    def run_load_dialect_hooks(cls, decorator: TypeDecorator, dialect: Dialect) -> Any:
        """Run load hooks and return first override result."""
        for hook in cls._load_dialect_hooks.get(type(decorator), []):
            result = hook(decorator, dialect)
            if result is not None:
                return result
        return None

    @classmethod
    def run_process_bind_hooks(cls, decorator: TypeDecorator, value: Any, dialect: Dialect) -> Any:
        """Run bind hooks and return first override result."""
        for hook in cls._process_bind_hooks.get(type(decorator), []):
            result = hook(decorator, value, dialect)
            if result is not None:
                return result
        return None

    @classmethod
    def run_process_result_hooks(cls, decorator: TypeDecorator, value: Any, dialect: Dialect) -> Any:
        """Run result hooks and return first override result."""
        for hook in cls._process_result_hooks.get(type(decorator), []):
            result = hook(decorator, value, dialect)
            if result is not None:
                return result
        return None


# Global class object used as unified registration entry.
GLOBAL_TYPE_DECORATOR_HOOK_REGISTRY = TypeDecoratorHookRegistry


def decode_content(content: Optional[dict[str, Any]]) -> Optional[Content]:
    """Decode a content object from a JSON dictionary.

    Args:
        content: JSON dictionary containing content data

    Returns:
        Decoded Content object or None if content is None
    """
    if not content:
        return None
    return Content.model_validate(content)


def decode_usage_metadata(usage_metadata: Optional[dict[str, Any]]) -> Optional[GenerateContentResponseUsageMetadata]:
    """Decode a usage metadata object from a JSON dictionary.

    Args:
        usage_metadata: JSON dictionary containing usage metadata

    Returns:
        Decoded GenerateContentResponseUsageMetadata object or None if usage_metadata is None
    """
    if not usage_metadata:
        return None
    return GenerateContentResponseUsageMetadata.model_validate(usage_metadata)


def decode_grounding_metadata(grounding_metadata: Optional[dict[str, Any]]) -> Optional[GroundingMetadata]:
    """Decode a grounding metadata object from a JSON dictionary.

    Args:
        grounding_metadata: JSON dictionary containing grounding metadata

    Returns:
        Decoded GroundingMetadata object or None if grounding_metadata is None
    """
    if not grounding_metadata:
        return None
    return GroundingMetadata.model_validate(grounding_metadata)


class DynamicJSONOptions:
    """A class for dynamic JSON dump options."""
    _json_dumps_kwargs: dict[str, Any] = {}  # pylint: disable=invalid-name
    _json_loads_kwargs: dict[str, Any] = {}  # pylint: disable=invalid-name

    @classmethod
    def set_json_dumps_kwargs(cls, options: dict[str, Any]) -> None:
        """Set the JSON dump options for the storage module."""
        cls._json_dumps_kwargs.update(options)

    @classmethod
    def set_json_loads_kwargs(cls, options: dict[str, Any]) -> None:
        """Set the JSON load options for the storage module."""
        cls._json_loads_kwargs.update(options)

    @classmethod
    def get_json_dumps_kwargs(cls) -> dict[str, Any]:
        """Get the JSON dump options for the storage module."""
        return cls._json_dumps_kwargs

    @classmethod
    def get_json_loads_kwargs(cls) -> dict[str, Any]:
        """Get the JSON load options for the storage module."""
        return cls._json_loads_kwargs


class DynamicJSON(TypeDecorator):
    """A JSON-like type that uses JSONB on PostgreSQL and TEXT with JSON serialization for other databases."""

    impl = Text  # Default implementation is TEXT

    def load_dialect_impl(self, dialect: Dialect) -> TypeDecorator:
        hook_result = GLOBAL_TYPE_DECORATOR_HOOK_REGISTRY.run_load_dialect_hooks(self, dialect)
        if hook_result is not None:
            return hook_result
        if dialect.name == "postgresql":
            return dialect.type_descriptor(postgresql.JSONB)  # type: ignore
        if dialect.name == "mysql":
            return dialect.type_descriptor(mysql.LONGTEXT)  # type: ignore
        return dialect.type_descriptor(Text)  # Default to Text for other dialects # type: ignore

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        hook_result = GLOBAL_TYPE_DECORATOR_HOOK_REGISTRY.run_process_bind_hooks(self, value, dialect)
        if hook_result is not None:
            return hook_result
        if value is not None:
            if dialect.name == "postgresql":
                return value  # JSONB handles dict directly
            # Serialize to JSON string for TEXT
            return json.dumps(value, **DynamicJSONOptions.get_json_dumps_kwargs())
        return value

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        hook_result = GLOBAL_TYPE_DECORATOR_HOOK_REGISTRY.run_process_result_hooks(self, value, dialect)
        if hook_result is not None:
            return hook_result
        if value is not None:
            if dialect.name == "postgresql":
                return value  # JSONB returns dict directly
            # Deserialize from JSON string for TEXT
            return json.loads(value, **DynamicJSONOptions.get_json_loads_kwargs())
        return value


class UTF8MB4String(TypeDecorator):
    """A String type that uses utf8mb4 charset and utf8mb4_unicode_ci collation for MySQL.

    This ensures proper handling of Unicode characters including emojis and Chinese characters.
    For other databases, it falls back to the standard String type.
    """

    impl = String
    cache_ok = True

    def __init__(self, length: Optional[int] = None, *args: Any, **kwargs: Any) -> None:
        super().__init__(length, *args, **kwargs)
        self.length = length

    def load_dialect_impl(self, dialect: Dialect) -> TypeDecorator:
        hook_result = GLOBAL_TYPE_DECORATOR_HOOK_REGISTRY.run_load_dialect_hooks(self, dialect)
        if hook_result is not None:
            return hook_result
        if dialect.name == "mysql":
            # Use VARCHAR with utf8mb4 charset and utf8mb4_unicode_ci collation
            return dialect.type_descriptor(mysql.VARCHAR(self.length, charset='utf8mb4',
                                                         collation='utf8mb4_unicode_ci'))  # type: ignore
        # For other databases, use standard String with the same length
        if self.length is not None:
            return dialect.type_descriptor(String(self.length))
        return dialect.type_descriptor(String())

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        hook_result = GLOBAL_TYPE_DECORATOR_HOOK_REGISTRY.run_process_bind_hooks(self, value, dialect)
        if hook_result is not None:
            return hook_result
        return value

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        hook_result = GLOBAL_TYPE_DECORATOR_HOOK_REGISTRY.run_process_result_hooks(self, value, dialect)
        if hook_result is not None:
            return hook_result
        return value


class PreciseTimestamp(TypeDecorator):
    """Represents a timestamp precise to the microsecond."""

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeDecorator:
        hook_result = GLOBAL_TYPE_DECORATOR_HOOK_REGISTRY.run_load_dialect_hooks(self, dialect)
        if hook_result is not None:
            return hook_result
        if dialect.name == "mysql":
            return dialect.type_descriptor(mysql.DATETIME(fsp=6))
        return self.impl

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        hook_result = GLOBAL_TYPE_DECORATOR_HOOK_REGISTRY.run_process_bind_hooks(self, value, dialect)
        if hook_result is not None:
            return hook_result
        return value

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        hook_result = GLOBAL_TYPE_DECORATOR_HOOK_REGISTRY.run_process_result_hooks(self, value, dialect)
        if hook_result is not None:
            return hook_result
        return value


class DynamicPickleType(TypeDecorator):
    """Represents a type that can be pickled."""

    impl = PickleType

    def load_dialect_impl(self, dialect: Dialect) -> TypeDecorator:
        hook_result = GLOBAL_TYPE_DECORATOR_HOOK_REGISTRY.run_load_dialect_hooks(self, dialect)
        if hook_result is not None:
            return hook_result
        if dialect.name == "spanner+spanner":
            return dialect.type_descriptor(SpannerPickleType)  # type: ignore
        if dialect.name == "mysql":
            return dialect.type_descriptor(mysql.LONGBLOB)  # type: ignore
        return self.impl

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        hook_result = GLOBAL_TYPE_DECORATOR_HOOK_REGISTRY.run_process_bind_hooks(self, value, dialect)
        if hook_result is not None:
            return hook_result
        if value is not None:
            if dialect.name == "spanner+spanner":
                return pickle.dumps(value)
        return value

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        hook_result = GLOBAL_TYPE_DECORATOR_HOOK_REGISTRY.run_process_result_hooks(self, value, dialect)
        if hook_result is not None:
            return hook_result
        if value is not None:
            if dialect.name == "spanner+spanner":
                return pickle.loads(value)
        return value


class SpannerPickleType(TypeDecorator):
    """Custom SQLAlchemy type for storing pickled data in Google Cloud Spanner.

    This type decorator handles base64 encoding/decoding of pickled data
    to ensure compatibility with Spanner's data storage requirements.
    """
    impl = PickleType

    def bind_processor(self, dialect: Dialect) -> Callable[[Any], Any]:  # pylint: disable=unused-argument
        """Process values when binding to database.

        Args:
            dialect: SQLAlchemy dialect instance

        Returns:
            Processing function that encodes pickled data to base64
        """

        def process(value: Any) -> Any:
            if value is None:
                return None
            return base64.standard_b64encode(value)

        return process

    # pylint: disable=unused-argument
    def result_processor(self, dialect: Dialect, coltype: Any) -> Callable[[Any], Any]:
        """Process values when retrieving from database.

        Args:
            dialect: SQLAlchemy dialect instance
            coltype: Column type information

        Returns:
            Processing function that decodes base64 data to pickled objects
        """

        def process(value: Any) -> Any:
            if value is None:
                return None
            return base64.standard_b64decode(value)

        return process


class StorageData(DeclarativeBase):
    """Base class for database tables."""

    pass
