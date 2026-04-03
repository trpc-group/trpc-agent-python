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
#
"""Evaluation set data structure."""

from __future__ import annotations

from typing import Optional

from ._common import EvalBaseModel
from ._eval_case import EvalCase


class EvalSet(EvalBaseModel):
    """A collection of evaluation test cases.

    Attributes:
        eval_set_id: Unique identifier for this eval set
        app_name: Optional default app name for session/result (used when case has no session_input.app_name)
        name: Human-readable name
        description: Description of what this eval set tests
        eval_cases: List of test cases in this set
        creation_timestamp: When this eval set was created
    """

    eval_set_id: str
    """Unique identifier for the eval set."""

    app_name: Optional[str] = None
    """Default app name for this eval set (session/result). Case session_input.app_name overrides when present."""

    name: Optional[str] = None
    """Name of the dataset."""

    description: Optional[str] = None
    """Description of the dataset."""

    eval_cases: list[EvalCase]
    """List of eval cases in the dataset."""

    creation_timestamp: float = 0.0
    """The time when this eval set was created."""
