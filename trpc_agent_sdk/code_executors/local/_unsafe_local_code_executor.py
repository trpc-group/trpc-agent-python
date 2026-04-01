# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unsafe local code executor for TRPC Agent framework.

This module provides a code executor that unsafely executes code in the current local context.
This executor is not recommended for production use due to security concerns.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing_extensions import override

from pydantic import Field

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.utils import async_execute_command

from .._base_code_executor import BaseCodeExecutor
from .._types import CodeBlock
from .._types import CodeBlockDelimiter
from .._types import CodeExecutionInput
from .._types import CodeExecutionResult
from .._types import create_code_execution_result


class UnsafeLocalCodeExecutor(BaseCodeExecutor):
    """A code executor that unsafely executes code in the current local context.

    WARNING: This executor is not recommended for production use due to security concerns.
    It executes code in the current process context without any Sandbox.
    """

    # Overrides the BaseCodeExecutor attribute: this executor cannot be stateful.
    stateful: bool = Field(default=False, frozen=True, exclude=True)

    # Overrides the BaseCodeExecutor attribute: this executor cannot optimize_data_file.
    optimize_data_file: bool = Field(default=False, frozen=True, exclude=True)

    work_dir: str = Field(default="", description="The working directory for the code execution.")

    timeout: float = Field(default=0, description="The timeout for the code execution.")

    clean_temp_files: bool = Field(default=True,
                                   description="Whether to clean temporary files after the code execution.")

    delimiter: CodeBlockDelimiter = Field(default_factory=CodeBlockDelimiter,
                                          description="The delimiter for the code execution.")

    def __init__(self, **data):
        """Initialize the UnsafeLocalCodeExecutor."""
        if "stateful" in data and data["stateful"]:
            raise ValueError("Cannot set `stateful=True` in UnsafeLocalCodeExecutor.")
        if "optimize_data_file" in data and data["optimize_data_file"]:
            raise ValueError("Cannot set `optimize_data_file=True` in UnsafeLocalCodeExecutor.")
        super().__init__(**data)

    @override
    async def execute_code(self, invocation_context: InvocationContext,
                           input_data: CodeExecutionInput) -> CodeExecutionResult:
        """Execute code blocks and return combined output.

        Args:
            invocation_context: The invocation context of the code execution.
            input_data: Code execution input

        Returns:
            CodeExecutionResult with combined output
        """
        output_parts = []
        error_parts = []
        if not input_data.code_blocks and input_data.code:
            # If no code blocks are provided, use the code as a single code block.
            input_data.code_blocks = [CodeBlock(code=input_data.code, language="python")]

        # Determine working directory
        work_dir, should_cleanup = self._prepare_work_dir(input_data.execution_id)

        try:
            # Execute each code block
            for i, block in enumerate(input_data.code_blocks):
                try:
                    block_output = await self._execute_code_block(work_dir, block, i)
                    if block_output:
                        output_parts.append(block_output)
                except Exception as ex:  # pylint: disable=broad-except
                    error_parts.append(f"Execution block {i} failed: {ex}")
        finally:
            # Cleanup if needed
            if should_cleanup:
                shutil.rmtree(work_dir, ignore_errors=True)

        return create_code_execution_result(stdout="\n".join(output_parts) if output_parts else "",
                                            stderr="\n".join(error_parts) if error_parts else "")

    @override
    def code_block_delimiter(self) -> CodeBlockDelimiter:
        """Return the code block delimiter used by this executor."""
        return self.delimiter

    def _prepare_work_dir(self, execution_id: str) -> tuple[Path, bool]:
        """Prepare working directory for execution.

        Args:
            execution_id: Unique execution identifier

        Returns:
            Tuple of (work_dir_path, should_cleanup)

        Raises:
            OSError: If directory creation fails
        """
        if self.work_dir:
            # Use configured work directory
            work_path = Path(self.work_dir)
            if not work_path.is_absolute():
                work_path = work_path.resolve()

            work_path.mkdir(parents=True, exist_ok=True)
            return work_path, False
        else:
            # Create temporary directory
            temp_dir = tempfile.mkdtemp(prefix=f"codeexec_{execution_id}_")
            return Path(temp_dir), self.clean_temp_files

    async def _execute_code_block(self, work_dir: Path, block: CodeBlock, block_index: int) -> str:
        """Execute a single code block.

        Args:
            work_dir: Working directory
            block: Code block to execute
            block_index: Index of the block

        Returns:
            Output from the execution

        Raises:
            ValueError: If language is unsupported
            subprocess.TimeoutExpired: If execution times out
            subprocess.CalledProcessError: If execution fails
        """
        # Prepare code file
        file_path = self._prepare_code_file(work_dir, block, block_index)

        # Build command arguments
        cmd_args = self._build_command_args(block.language, file_path)

        # Execute command
        result = await async_execute_command(work_dir=work_dir, cmd_args=cmd_args, timeout=self.timeout)
        if result.exit_code != 0 or result.is_timeout:
            error_msg = result.stderr if result.stderr else f"Command failed with return code {result.exit_code}"
            raise RuntimeError(error_msg)
        return result.stdout

    def _prepare_code_file(self, work_dir: Path, block: CodeBlock, block_index: int) -> Path:
        """Write code to a temporary file.

        Args:
            work_dir: Working directory
            block: Code block
            block_index: Index of the block

        Returns:
            Path to the created file

        Raises:
            ValueError: If language is unsupported
            OSError: If file write fails
        """
        language = block.language.lower()

        # Determine file extension
        if language in ("python", "py", "python3"):
            ext = ".py"
            file_mode = 0o644
        elif language in ("bash", "sh"):
            ext = ".sh"
            file_mode = 0o755
        else:
            raise ValueError(f"unsupported language: {block.language}")

        # Create file path
        file_name = f"code_{block_index}{ext}"
        file_path = work_dir / file_name

        # Prepare content
        content = block.code.strip()

        # For Python, ensure newline at end if no print statements
        if language in ("python", "py", "python3"):
            if "print(" not in content and "sys.stdout.write(" not in content:
                content += "\n"

        # Write file
        file_path.write_text(content, encoding="utf-8")
        file_path.chmod(file_mode)

        return file_path

    def _build_command_args(self, language: str, file_path: Path) -> list[str]:
        """Build command arguments for executing the code file.

        Args:
            language: Programming language
            file_path: Path to the code file

        Returns:
            List of command arguments, or empty list if unsupported
        """
        language = language.lower()

        if language in ("python", "py", "python3"):
            return ["python3", str(file_path)]
        elif language in ("bash", "sh"):
            return ["bash", str(file_path)]
        else:
            raise ValueError(f"unsupported language: {language}")
