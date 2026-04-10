# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Container code executor for TRPC Agent framework.

This module provides a code executor that uses a custom container to execute code.
This executor provides better isolation and security compared to unsafe local execution.
"""

from __future__ import annotations

from typing import Optional
from typing_extensions import override

from pydantic import Field
from trpc_agent_sdk.context import InvocationContext

from .._base_code_executor import BaseCodeExecutor
from .._types import CodeBlockDelimiter
from .._types import CodeExecutionInput
from .._types import CodeExecutionResult
from .._types import create_code_execution_result
from ._container_cli import CommandArgs
from ._container_cli import ContainerClient
from ._container_cli import ContainerConfig


class ContainerCodeExecutor(BaseCodeExecutor):
    """A code executor that uses a custom container to execute code.

    Attributes:
        base_url: Optional. The base url of the user hosted Docker client.
        image: The tag of the predefined image or custom image to run on the
            container. Either docker_path or image must be set.
        docker_path: The path to the directory containing the Dockerfile. If set,
            build the image from the dockerfile path instead of using the predefined
            image. Either docker_path or image must be set.
    """

    base_url: Optional[str] = None
    """Optional. The base url of the user hosted Docker client."""

    image: Optional[str] = None
    """The tag of the predefined image or custom image to run on the container.
    Either docker_path or image must be set.
    """

    docker_path: Optional[str] = None
    """The path to the directory containing the Dockerfile.
    If set, build the image from the dockerfile path instead of using the
    predefined image. Either docker_path or image must be set.
    """
    timeout: Optional[float] = None
    """The timeout for the code execution in seconds."""

    environment: Optional[dict[str, str]] = None
    """The environment variables to set for the code execution."""

    # Overrides the BaseCodeExecutor attribute: this executor cannot be stateful.
    stateful: bool = Field(default=False, frozen=True, exclude=True)

    # Overrides the BaseCodeExecutor attribute: this executor cannot optimize_data_file.
    optimize_data_file: bool = Field(default=False, frozen=True, exclude=True)

    _container: ContainerClient = None
    """The container instance."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        image: Optional[str] = None,
        docker_path: Optional[str] = None,
        **data,
    ):
        """Initialize the ContainerCodeExecutor.

        Args:
            base_url: Optional. The base url of the user hosted Docker client.
            image: The tag of the predefined image or custom image to run on the
                container. Either docker_path or image must be set.
            docker_path: The path to the directory containing the Dockerfile. If set,
                build the image from the dockerfile path instead of using the predefined
                image. Either docker_path or image must be set.
            **data: The data to initialize the ContainerCodeExecutor.
        """
        if not image and not docker_path:
            raise ValueError('Either image or docker_path must be set for ContainerCodeExecutor.')
        if 'stateful' in data and data['stateful']:
            raise ValueError('Cannot set `stateful=True` in ContainerCodeExecutor.')
        if 'optimize_data_file' in data and data['optimize_data_file']:
            raise ValueError('Cannot set `optimize_data_file=True` in ContainerCodeExecutor.')

        super().__init__(**data)
        self.base_url = base_url
        self.image = image
        self.docker_path = docker_path
        if not self._container:
            self._container = ContainerClient(
                config=ContainerConfig(base_url=base_url, image=image, docker_path=docker_path))
        if not self._container:
            raise Exception("Container not initialized")

    @override
    async def execute_code(
        self,
        invocation_context: InvocationContext,
        code_execution_input: CodeExecutionInput,
    ) -> CodeExecutionResult:
        """Execute code in the container.

        Args:
            invocation_context: The invocation context of the code execution.
            code_execution_input: The code execution input.

        Returns:
            The code execution response.
        """
        all_output = []
        all_errors = []

        # Execute each code block
        for block in code_execution_input.code_blocks:
            exec_cmd = []

            # Determine command based on language
            language = block.language.lower() if block.language else ""

            if language in ["bash", "sh"]:
                exec_cmd = ["/bin/bash", "-c", block.code]
            elif language in ["python", "py", "python3", ""]:
                # Default to python if no language specified
                exec_cmd = ["python3", "-c", block.code]
            else:
                # Unsupported language
                error_msg = f"unsupported language: {block.language}\n"
                all_errors.append(error_msg)
                continue
            command_args = CommandArgs(environment=self.environment, timeout=self.timeout)
            output = await self._container.exec_run(cmd=exec_cmd, command_args=command_args)
            if output.exit_code:
                all_errors.append(output.stderr)
                continue
            else:
                all_output.append(output.stdout)

        # Combine stdout and stderr
        output = "".join(all_output)
        err_str = "".join(all_errors)
        return create_code_execution_result(stdout=output, stderr=err_str)

    @override
    def code_block_delimiter(self) -> CodeBlockDelimiter:
        """Return the code block delimiter used by this executor.

        Returns:
            CodeBlockDelimiter instance
        """
        return CodeBlockDelimiter(start="```tool_code\n", end="\n```")
