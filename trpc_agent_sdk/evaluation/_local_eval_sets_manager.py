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
"""Local evaluation sets manager that stores eval sets on disk."""

from __future__ import annotations

import json
import os
import re
import time
from typing import Optional
from typing_extensions import override

from pydantic import ValidationError

from trpc_agent_sdk.log import error as log_error
from trpc_agent_sdk.log import info as log_info
from trpc_agent_sdk.log import warning as log_warning

from ._eval_case import EvalCase
from ._eval_set import EvalSet
from ._eval_sets_manager_base import EvalSetsManager
from ._eval_sets_manager_utils import add_eval_case_to_eval_set
from ._eval_sets_manager_utils import delete_eval_case_from_eval_set
from ._eval_sets_manager_utils import get_eval_case_from_eval_set
from ._eval_sets_manager_utils import get_eval_set_from_app_and_id
from ._eval_sets_manager_utils import update_eval_case_in_eval_set

_EVAL_SET_FILE_EXTENSION = ".evalset.json"


def load_eval_set_from_file(eval_set_file_path: str, eval_set_id: str) -> EvalSet:
    """Returns an EvalSet that is read from the given file."""
    with open(eval_set_file_path, "r", encoding="utf-8") as f:
        content = f.read()
        try:
            return EvalSet.model_validate_json(content)
        except ValidationError:
            # Try to load as old format (list of eval cases)
            try:
                old_format_data = json.loads(content)
                if isinstance(old_format_data, list):
                    # Convert old format to new format
                    eval_cases = []
                    for old_case in old_format_data:
                        # Convert old format eval case to new format
                        # This is a simplified conversion - may need adjustment
                        eval_case = EvalCase(
                            eval_id=old_case.get("name", f"case_{len(eval_cases)}"),
                            conversation=[],
                            session_input=None,
                            creation_timestamp=time.time(),
                        )
                        eval_cases.append(eval_case)

                    return EvalSet(
                        eval_set_id=eval_set_id,
                        name=eval_set_id,
                        eval_cases=eval_cases,
                        creation_timestamp=time.time(),
                    )
            except Exception as ex:  # pylint: disable=broad-except
                log_error("Failed to load eval set from %s: %s", eval_set_file_path, ex)
                raise ValueError(f"Failed to load eval set from {eval_set_file_path}: {ex}") from ex


class LocalEvalSetsManager(EvalSetsManager):
    """An EvalSets manager that stores eval sets locally on disk."""

    def __init__(self, agents_dir: str):
        """Initialize the local eval sets manager.

        Args:
            agents_dir: Base directory where agents are stored
        """
        self._agents_dir = agents_dir

    @override
    def get_eval_set(self, app_name: str, eval_set_id: str) -> Optional[EvalSet]:
        """Returns an EvalSet identified by an app_name and eval_set_id."""
        # Try multiple possible paths
        possible_paths = [
            # Standard path: {agents_dir}/{app_name}/{eval_set_id}.evalset.json
            os.path.join(
                self._agents_dir,
                app_name,
                eval_set_id + _EVAL_SET_FILE_EXTENSION,
            ),
        ]

        for eval_set_file_path in possible_paths:
            if os.path.exists(eval_set_file_path):
                try:
                    return load_eval_set_from_file(eval_set_file_path, eval_set_id)
                except Exception as ex:  # pylint: disable=broad-except
                    log_warning("Failed to load eval set from %s: %s", eval_set_file_path, ex)
                    continue

        return None

    @override
    def create_eval_set(self, app_name: str, eval_set_id: str) -> EvalSet:
        """Creates and returns an empty EvalSet given the app_name and eval_set_id.

        Raises:
            ValueError: If Eval Set ID is not valid or an eval set already exists.
        """
        self._validate_id(id_name="Eval Set ID", id_value=eval_set_id)

        # Use standard path: {agents_dir}/{app_name}/{eval_set_id}.evalset.json
        new_eval_set_path = os.path.join(
            self._agents_dir,
            app_name,
            eval_set_id + _EVAL_SET_FILE_EXTENSION,
        )

        log_info("Creating eval set file `%s`", new_eval_set_path)

        if not os.path.exists(new_eval_set_path):
            # Write the JSON string to the file
            log_info("Eval set file doesn't exist, we will create a new one.")
            new_eval_set = EvalSet(
                eval_set_id=eval_set_id,
                name=eval_set_id,
                eval_cases=[],
                creation_timestamp=time.time(),
            )
            self._write_eval_set_to_path(new_eval_set_path, new_eval_set)
            return new_eval_set

        raise ValueError(f"EvalSet {eval_set_id} already exists for app {app_name}.")

    @override
    def list_eval_sets(self, app_name: str) -> list[str]:
        """Returns a list of EvalSets that belong to the given app_name.

        Args:
            app_name: The app name to list the eval sets for.

        Returns:
            A list of EvalSet ids.
        """
        eval_sets = []

        # Try standard path first: {agents_dir}/{app_name}/
        app_dir = os.path.join(self._agents_dir, app_name)
        if os.path.exists(app_dir):
            try:
                for file in os.listdir(app_dir):
                    if file.endswith(_EVAL_SET_FILE_EXTENSION):
                        eval_set_id = file.removesuffix(_EVAL_SET_FILE_EXTENSION)
                        eval_sets.append(eval_set_id)
            except Exception as ex:  # pylint: disable=broad-except
                log_warning("Failed to list eval sets from %s: %s", app_dir, ex)

        return sorted(eval_sets)

    @override
    def get_eval_case(self, app_name: str, eval_set_id: str, eval_case_id: str) -> Optional[EvalCase]:
        """Returns an EvalCase if found; otherwise, None."""
        eval_set = self.get_eval_set(app_name, eval_set_id)
        if not eval_set:
            return None
        return get_eval_case_from_eval_set(eval_set, eval_case_id)

    @override
    def add_eval_case(self, app_name: str, eval_set_id: str, eval_case: EvalCase):
        """Adds the given EvalCase to an existing EvalSet identified by app_name and eval_set_id.

        Raises:
            NotFoundError: If the eval set is not found.
        """
        eval_set = get_eval_set_from_app_and_id(self, app_name, eval_set_id)
        updated_eval_set = add_eval_case_to_eval_set(eval_set, eval_case)

        self._save_eval_set(app_name, eval_set_id, updated_eval_set)

    @override
    def update_eval_case(self, app_name: str, eval_set_id: str, updated_eval_case: EvalCase):
        """Updates an existing EvalCase given the app_name and eval_set_id.

        Raises:
            NotFoundError: If the eval set or the eval case is not found.
        """
        eval_set = get_eval_set_from_app_and_id(self, app_name, eval_set_id)
        updated_eval_set = update_eval_case_in_eval_set(eval_set, updated_eval_case)
        self._save_eval_set(app_name, eval_set_id, updated_eval_set)

    @override
    def delete_eval_case(self, app_name: str, eval_set_id: str, eval_case_id: str):
        """Deletes the given EvalCase identified by app_name, eval_set_id and eval_case_id.

        Raises:
            NotFoundError: If the eval set or the eval case to delete is not found.
        """
        eval_set = get_eval_set_from_app_and_id(self, app_name, eval_set_id)
        updated_eval_set = delete_eval_case_from_eval_set(eval_set, eval_case_id)
        self._save_eval_set(app_name, eval_set_id, updated_eval_set)

    def _get_eval_set_file_path(self, app_name: str, eval_set_id: str) -> str:
        """Get the file path for an eval set.

        Uses standard path: {agents_dir}/{app_name}/{eval_set_id}.evalset.json
        """
        return os.path.join(
            self._agents_dir,
            app_name,
            eval_set_id + _EVAL_SET_FILE_EXTENSION,
        )

    def _validate_id(self, id_name: str, id_value: str):
        """Validate an ID format."""
        pattern = r"^[a-zA-Z0-9_]+$"
        if not bool(re.fullmatch(pattern, id_value)):
            raise ValueError(f"Invalid {id_name}. {id_name} should have the `{pattern}` format", )

    def _write_eval_set_to_path(self, eval_set_path: str, eval_set: EvalSet):
        """Write an eval set to a file path."""
        os.makedirs(os.path.dirname(eval_set_path), exist_ok=True)
        with open(eval_set_path, "w", encoding="utf-8") as f:
            f.write(eval_set.model_dump_json(
                indent=2,
                exclude_unset=True,
                exclude_defaults=True,
                exclude_none=True,
            ))

    def _save_eval_set(self, app_name: str, eval_set_id: str, eval_set: EvalSet):
        """Save an eval set to disk."""
        eval_set_file_path = self._get_eval_set_file_path(app_name, eval_set_id)
        self._write_eval_set_to_path(eval_set_file_path, eval_set)
