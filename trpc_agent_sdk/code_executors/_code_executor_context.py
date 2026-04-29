# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Code executor context for TRPC Agent framework.

This module provides context management for code execution, including
state tracking, file management, and error handling.
"""

from typing import Dict
from typing import List
from typing import Optional

from ._types import CodeBlock
from ._types import CodeExecutionResult
from ._types import CodeFile


class CodeExecutorContext:
    """Context for managing code execution state and files."""

    def __init__(self, session_state: Dict):
        """Initialize the code executor context.

        Args:
            session_state: The session state dictionary.
        """
        self.session_state = session_state
        self._ensure_code_execution_state()

    def _ensure_code_execution_state(self) -> None:
        """Ensure code execution state exists in session."""
        if "code_execution" not in self.session_state:
            self.session_state["code_execution"] = {
                "input_files": [],
                "processed_file_names": [],
                "execution_id": None,
                "error_counts": {},
                "code_execution_results": {},
            }

    def get_input_files(self) -> List[CodeFile]:
        """Get input files from context.

        Returns:
            List of input files.
        """
        return self.session_state["code_execution"]["input_files"]

    def add_input_files(self, files: List[CodeFile]) -> None:
        """Add input files to context.

        Args:
            files: List of files to add.
        """
        self.session_state["code_execution"]["input_files"].extend(files)

    def get_processed_file_names(self) -> List[str]:
        """Get processed file names.

        Returns:
            List of processed file names.
        """
        return self.session_state["code_execution"]["processed_file_names"]

    def add_processed_file_names(self, file_names: List[str]) -> None:
        """Add processed file names.

        Args:
            file_names: List of file names to add.
        """
        self.session_state["code_execution"]["processed_file_names"].extend(file_names)

    def get_execution_id(self) -> Optional[str]:
        """Get execution ID.

        Returns:
            Execution ID or None.
        """
        return self.session_state["code_execution"]["execution_id"]

    def set_execution_id(self, execution_id: str) -> None:
        """Set execution ID.

        Args:
            execution_id: The execution ID to set.
        """
        self.session_state["code_execution"]["execution_id"] = execution_id

    def get_error_count(self, invocation_id: str) -> int:
        """Get error count for an invocation.

        Args:
            invocation_id: The invocation ID.

        Returns:
            Error count.
        """
        return self.session_state["code_execution"]["error_counts"].get(invocation_id, 0)

    def increment_error_count(self, invocation_id: str) -> None:
        """Increment error count for an invocation.

        Args:
            invocation_id: The invocation ID.
        """
        if invocation_id not in self.session_state["code_execution"]["error_counts"]:
            self.session_state["code_execution"]["error_counts"][invocation_id] = 0
        self.session_state["code_execution"]["error_counts"][invocation_id] += 1

    def reset_error_count(self, invocation_id: str) -> None:
        """Reset error count for an invocation.

        Args:
            invocation_id: The invocation ID.
        """
        if invocation_id in self.session_state["code_execution"]["error_counts"]:
            self.session_state["code_execution"]["error_counts"][invocation_id] = 0

    def update_code_execution_result(self, invocation_id: str, code_blocks: List[CodeBlock],
                                     code_execution_result: CodeExecutionResult) -> None:
        """Update code execution result.

        Args:
            invocation_id: The invocation ID.
            code_blocks: The code blocks.
            result: The code execution result.
        """
        if invocation_id not in self.session_state["code_execution"]["code_execution_results"]:
            self.session_state["code_execution"]["code_execution_results"][invocation_id] = []
        code = '\n'.join([code_block.code for code_block in code_blocks])
        self.session_state["code_execution"]["code_execution_results"][invocation_id].append({
            "code":
            code,
            "result":
            code_execution_result.model_dump(),
        })

    def get_state_delta(self) -> Dict:
        """Get state delta for the current execution.

        Returns:
            State delta dictionary.
        """
        return {"code_execution": self.session_state["code_execution"]}
