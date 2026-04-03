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
"""In-memory evaluation sets manager.

This module provides an in-memory implementation of EvalSetsManager using
dictionaries. You can use this class:
1) As a part of your testcase.
2) For cases where other implementations of EvalSetsManager are too expensive to use.
"""

from __future__ import annotations

import time
from typing import Optional
from typing_extensions import override

from ._eval_case import EvalCase
from ._eval_set import EvalSet
from ._eval_sets_manager_base import EvalSetsManager


class InMemoryEvalSetsManager(EvalSetsManager):
    """In-memory implementation of evaluation sets manager using dictionaries.

    This class uses dual-layer indexing for efficient O(1) eval case lookups.
    You can use this class:
    1) As a part of your testcase.
    2) For cases where other implementations of EvalSetsManager are too expensive to use.
    """

    def __init__(self):
        """Initialize the in-memory eval sets manager."""
        # {app_name: {eval_set_id: EvalSet}}
        self._eval_sets: dict[str, dict[str, EvalSet]] = {}
        # {app_name: {eval_set_id: {eval_case_id: EvalCase}}}
        self._eval_cases: dict[str, dict[str, dict[str, EvalCase]]] = {}

    def _ensure_app_exists(self, app_name: str):
        """Ensure the app exists in storage."""
        if app_name not in self._eval_sets:
            self._eval_sets[app_name] = {}
            self._eval_cases[app_name] = {}

    @override
    def get_eval_set(self, app_name: str, eval_set_id: str) -> Optional[EvalSet]:
        """Get an eval set by ID.

        Args:
            app_name: Application name
            eval_set_id: ID of the eval set

        Returns:
            EvalSet if found, None otherwise
        """
        self._ensure_app_exists(app_name)
        return self._eval_sets[app_name].get(eval_set_id, None)

    @override
    def create_eval_set(self, app_name: str, eval_set_id: str) -> EvalSet:
        """Create a new eval set.

        Args:
            app_name: Application name
            eval_set_id: ID for the new eval set

        Returns:
            The created EvalSet

        Raises:
            ValueError: If eval set already exists
        """
        self._ensure_app_exists(app_name)

        if eval_set_id in self._eval_sets[app_name]:
            raise ValueError(f"EvalSet {eval_set_id} already exists for app {app_name}.")

        new_eval_set = EvalSet(
            eval_set_id=eval_set_id,
            eval_cases=[],
            creation_timestamp=time.time(),
        )
        self._eval_sets[app_name][eval_set_id] = new_eval_set
        self._eval_cases[app_name][eval_set_id] = {}
        return new_eval_set

    @override
    def list_eval_sets(self, app_name: str) -> list[str]:
        """List all eval set IDs for an application.

        Args:
            app_name: Application name

        Returns:
            List of eval set IDs
        """
        if app_name not in self._eval_sets:
            return []

        return list(self._eval_sets[app_name].keys())

    @override
    def get_eval_case(self, app_name: str, eval_set_id: str, eval_case_id: str) -> Optional[EvalCase]:
        """Get a specific eval case.

        Args:
            app_name: Application name
            eval_set_id: ID of the eval set
            eval_case_id: ID of the eval case

        Returns:
            EvalCase if found, None otherwise
        """
        if app_name not in self._eval_cases:
            return None
        if eval_set_id not in self._eval_cases[app_name]:
            return None
        return self._eval_cases[app_name][eval_set_id].get(eval_case_id)

    @override
    def add_eval_case(self, app_name: str, eval_set_id: str, eval_case: EvalCase):
        """Add an eval case to an eval set.

        Args:
            app_name: Application name
            eval_set_id: ID of the eval set
            eval_case: The eval case to add

        Raises:
            ValueError: If eval set not found or eval case already exists
        """
        self._ensure_app_exists(app_name)

        if eval_set_id not in self._eval_sets[app_name]:
            raise ValueError(f"EvalSet {eval_set_id} not found for app {app_name}.")

        if eval_case.eval_id in self._eval_cases[app_name][eval_set_id]:
            raise ValueError(f"EvalCase {eval_case.eval_id} already exists in EvalSet"
                             f" {eval_set_id} for app {app_name}.")

        self._eval_cases[app_name][eval_set_id][eval_case.eval_id] = eval_case
        # Also update the list in the EvalSet object
        self._eval_sets[app_name][eval_set_id].eval_cases.append(eval_case)

    @override
    def update_eval_case(self, app_name: str, eval_set_id: str, updated_eval_case: EvalCase):
        """Update an existing eval case.

        Args:
            app_name: Application name
            eval_set_id: ID of the eval set
            updated_eval_case: The updated eval case

        Raises:
            ValueError: If eval set or eval case not found
        """
        self._ensure_app_exists(app_name)

        if eval_set_id not in self._eval_sets[app_name]:
            raise ValueError(f"EvalSet {eval_set_id} not found for app {app_name}.")

        if updated_eval_case.eval_id not in self._eval_cases[app_name][eval_set_id]:
            raise ValueError(f"EvalCase {updated_eval_case.eval_id} not found in EvalSet"
                             f" {eval_set_id} for app {app_name}.")

        # Full replace in index
        self._eval_cases[app_name][eval_set_id][updated_eval_case.eval_id] = updated_eval_case

        # Update the list in the EvalSet object
        eval_set = self._eval_sets[app_name][eval_set_id]
        for i, case in enumerate(eval_set.eval_cases):
            if case.eval_id == updated_eval_case.eval_id:
                eval_set.eval_cases[i] = updated_eval_case
                break

    @override
    def delete_eval_case(self, app_name: str, eval_set_id: str, eval_case_id: str):
        """Delete an eval case from an eval set.

        Args:
            app_name: Application name
            eval_set_id: ID of the eval set
            eval_case_id: ID of the eval case to delete

        Raises:
            ValueError: If eval set or eval case not found
        """
        self._ensure_app_exists(app_name)

        if eval_set_id not in self._eval_sets[app_name]:
            raise ValueError(f"EvalSet {eval_set_id} not found for app {app_name}.")

        if eval_case_id not in self._eval_cases[app_name][eval_set_id]:
            raise ValueError(f"EvalCase {eval_case_id} not found in EvalSet {eval_set_id}"
                             f" for app {app_name}.")

        # Delete from index
        del self._eval_cases[app_name][eval_set_id][eval_case_id]

        # Remove from the list in the EvalSet object
        eval_set = self._eval_sets[app_name][eval_set_id]
        eval_set.eval_cases = [case for case in eval_set.eval_cases if case.eval_id != eval_case_id]
