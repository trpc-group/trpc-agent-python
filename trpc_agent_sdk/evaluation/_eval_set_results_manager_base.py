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
"""Evaluation set results manager interface.

"""

from __future__ import annotations

from abc import ABC
from abc import abstractmethod

from ._eval_result import EvalCaseResult
from ._eval_result import EvalSetResult


class EvalSetResultsManager(ABC):
    """An interface to manage Eval Set Results."""

    @abstractmethod
    def save_eval_set_result(
        self,
        app_name: str,
        eval_set_id: str,
        eval_case_results: list[EvalCaseResult],
    ) -> None:
        """Creates and saves a new EvalSetResult given eval_case_results.

        Args:
            app_name: Name of the application
            eval_set_id: ID of the eval set
            eval_case_results: List of eval case results
        """
        ...

    @abstractmethod
    def get_eval_set_result(self, app_name: str, eval_set_result_id: str) -> EvalSetResult:
        """Returns the EvalSetResult from app_name and eval_set_result_id.

        Args:
            app_name: Name of the application
            eval_set_result_id: ID of the eval set result

        Returns:
            EvalSetResult for the given IDs

        Raises:
            FileNotFoundError: If the EvalSetResult is not found.
        """
        ...

    @abstractmethod
    def list_eval_set_results(self, app_name: str) -> list[str]:
        """Returns the eval result ids that belong to the given app_name.

        Args:
            app_name: Name of the application

        Returns:
            List of eval set result IDs
        """
        ...
