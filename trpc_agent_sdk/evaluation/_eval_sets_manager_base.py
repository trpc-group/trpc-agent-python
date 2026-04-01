# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
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
"""Evaluation sets manager interface.

"""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from typing import Optional

from ._eval_case import EvalCase
from ._eval_set import EvalSet


class EvalSetsManager(ABC):
    """Abstract interface for managing evaluation sets.

    This provides CRUD operations for evaluation sets and their cases.
    """

    @abstractmethod
    def get_eval_set(self, app_name: str, eval_set_id: str) -> Optional[EvalSet]:
        """Get an eval set by ID.

        Args:
            app_name: Application name
            eval_set_id: ID of the eval set

        Returns:
            EvalSet if found, None otherwise
        """
        ...

    @abstractmethod
    def create_eval_set(self, app_name: str, eval_set_id: str) -> EvalSet:
        """Create a new eval set.

        Args:
            app_name: Application name
            eval_set_id: ID for the new eval set

        Returns:
            The created EvalSet
        """
        ...

    @abstractmethod
    def list_eval_sets(self, app_name: str) -> list[str]:
        """List all eval set IDs for an application.

        Args:
            app_name: Application name

        Returns:
            List of eval set IDs
        """
        ...

    @abstractmethod
    def get_eval_case(self, app_name: str, eval_set_id: str, eval_case_id: str) -> Optional[EvalCase]:
        """Get a specific eval case.

        Args:
            app_name: Application name
            eval_set_id: ID of the eval set
            eval_case_id: ID of the eval case

        Returns:
            EvalCase if found, None otherwise
        """
        ...

    @abstractmethod
    def add_eval_case(self, app_name: str, eval_set_id: str, eval_case: EvalCase):
        """Add an eval case to an eval set.

        Args:
            app_name: Application name
            eval_set_id: ID of the eval set
            eval_case: The eval case to add
        """
        ...

    @abstractmethod
    def update_eval_case(self, app_name: str, eval_set_id: str, updated_eval_case: EvalCase):
        """Update an existing eval case.

        Args:
            app_name: Application name
            eval_set_id: ID of the eval set
            updated_eval_case: The updated eval case
        """
        ...

    @abstractmethod
    def delete_eval_case(self, app_name: str, eval_set_id: str, eval_case_id: str):
        """Delete an eval case from an eval set.

        Args:
            app_name: Application name
            eval_set_id: ID of the eval set
            eval_case_id: ID of the eval case to delete
        """
        ...
