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
"""Metadata utilities using unprefixed keys."""

from __future__ import annotations

from typing import Any
from typing import Optional


def set_metadata(metadata: dict[str, Any], key: str, value: Any) -> None:
    """Set a metadata value for the given key."""
    metadata[key] = value


def get_metadata(
    metadata: Optional[dict[str, Any]],
    key: str,
    default: Any = None,
) -> Any:
    """Get a metadata value by key."""
    if not metadata:
        return default
    return metadata.get(key, default)


def metadata_is_true(metadata: Optional[dict[str, Any]], key: str) -> bool:
    """Return whether a metadata key is set to a truthy boolean value."""
    value = get_metadata(metadata, key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False
