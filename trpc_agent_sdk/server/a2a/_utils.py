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
"""Metadata utilities using unprefixed keys.

In a2a-sdk 1.x metadata fields are ``google.protobuf.Struct`` instances
instead of plain ``dict``.  These helpers duck-type both so callers keep
working whether they hold a ``dict`` or a ``Struct``.
"""

from __future__ import annotations

from typing import Any
from typing import Optional

from google.protobuf import struct_pb2


def set_metadata(metadata: Any, key: str, value: Any) -> None:
    """Set a metadata value for the given key.

    Works on both plain ``dict`` and ``google.protobuf.Struct``.  For a
    ``Struct`` the value is converted via ``ParseDict`` (plain dict) or
    ``Struct``-compatible assignment (scalars/lists), so structured values
    are persisted correctly on the wire.
    """
    if isinstance(metadata, struct_pb2.Struct):
        # Struct supports mapping-style updates for dict/list/scalar values.
        metadata.update({key: value})
        return
    metadata[key] = value


def get_metadata(
    metadata: Optional[Any],
    key: str,
    default: Any = None,
) -> Any:
    """Get a metadata value by key.

    Works on both plain ``dict`` and ``google.protobuf.Struct``.  For a
    ``Struct``, numeric values round-trip through protobuf ``Value`` and may
    arrive as ``float``.
    """
    if not metadata:
        return default
    try:
        if key in metadata:
            return metadata[key]
    except TypeError:  # pragma: no cover - defensive
        pass
    return default


def metadata_is_true(metadata: Optional[Any], key: str) -> bool:
    """Return whether a metadata key is set to a truthy boolean value."""
    value = get_metadata(metadata, key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False
