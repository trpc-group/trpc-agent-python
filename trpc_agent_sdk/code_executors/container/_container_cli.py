# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Container code executor for TRPC Agent framework.

This module provides a code executor that uses a custom container to execute code.
This executor provides better isolation and security compared to unsafe local execution.
"""

from __future__ import annotations

import asyncio
import atexit
import os
from dataclasses import dataclass
from typing import Optional

import docker
from docker.models.containers import Container

from trpc_agent_sdk.log import logger
from trpc_agent_sdk.utils import CommandExecResult

DEFAULT_IMAGE_TAG = 'python:3-slim'


@dataclass
class ContainerConfig:
    """Configuration for container."""
    base_url: Optional[str] = None
    """The base url of the user hosted Docker client."""
    image: str = DEFAULT_IMAGE_TAG
    """The tag of the predefined image or custom image to run on the container.
    Either docker_path or image must be set.
    """
    docker_path: Optional[str] = None
    """The path to the Docker file to build the image from."""
    host_config: Optional[dict] = None
    """Optional host config (for example {"Binds": ["/host:/container:ro"]})."""


@dataclass
class CommandArgs:
    """Command arguments."""
    environment: Optional[dict[str, str]] = None
    """The environment variables for the command execution."""
    timeout: Optional[float] = None
    """The timeout for the command execution in seconds."""


class ContainerClient:
    """Container CLI client class."""

    def __init__(self, config: ContainerConfig):
        """Initialize the container."""
        self.base_url = config.base_url
        self.image = config.image
        self.docker_path = os.path.abspath(config.docker_path) if config.docker_path else None
        self.host_config = config.host_config or {}
        self._client = None
        self._container = None
        self._init_docker_client()
        self._init_container()
        atexit.register(self._cleanup_container)

    @property
    def client(self) -> docker.DockerClient:
        """Get the Docker client."""
        return self._client

    @property
    def container(self) -> Container:
        """Get the container."""
        return self._container

    def _init_docker_client(self):
        """Initialize the Docker client with comprehensive error handling.

        This method attempts to connect to Docker using docker SDK's from_env()
        which handles various Docker connection methods including:
        - Standard Unix socket (/var/run/docker.sock)
        - Docker Desktop (Windows/Mac)
        - Remote Docker via DOCKER_HOST environment variable
        - Custom base_url if provided
        """
        # Try to initialize Docker client
        # Let docker SDK handle connection detection (it supports various methods)
        try:
            if self.base_url:
                # Use custom base_url if provided
                self._client = docker.DockerClient(base_url=self.base_url)
            else:
                # Use docker.from_env() which automatically detects:
                # - DOCKER_HOST environment variable
                # - Standard socket paths
                # - Docker Desktop configurations
                self._client = docker.from_env()

            # Test connection by pinging Docker daemon
            # This will fail if Docker is not running or not accessible
            self._client.ping()
            logger.info("Docker client initialized successfully")
        except docker.errors.DockerException as ex:
            # Extract more specific error information
            error_str = str(ex)

            # Check if it's a connection error
            if "Connection" in error_str or "socket" in error_str.lower() or "No such file" in error_str:
                error_msg = ("Failed to connect to Docker daemon. Docker may not be running or accessible.\n\n"
                             "Common solutions:\n"
                             "  1. Start Docker daemon:\n"
                             "     - Linux: sudo systemctl start docker\n"
                             "     - Windows/Mac: Start Docker Desktop application\n"
                             "  2. Verify Docker is running: docker ps\n"
                             "  3. Check Docker socket permissions (Linux):\n"
                             "     - sudo chmod 666 /var/run/docker.sock\n"
                             "     - Or add your user to docker group: sudo usermod -aG docker $USER\n"
                             "  4. For Docker Desktop, ensure it's fully started (check system tray)\n"
                             "  5. Check DOCKER_HOST environment variable if using remote Docker\n"
                             "  6. If using remote Docker, set base_url parameter in ContainerCodeExecutor\n\n"
                             f"Original error: {error_str}")
            else:
                error_msg = (f"Failed to connect to Docker daemon: {error_str}\n\n"
                             "Please ensure:\n"
                             "  1. Docker daemon is running: docker ps\n"
                             "  2. You have permission to access Docker\n"
                             "  3. Docker is properly installed and configured")
            raise RuntimeError(error_msg) from ex
        except Exception as ex:  # pylint: disable=broad-except
            error_msg = (f"Unexpected error initializing Docker client: {str(ex)}\n\n"
                         "Please check:\n"
                         "  1. Docker installation: docker --version\n"
                         "  2. Docker daemon status: docker ps\n"
                         "  3. Docker SDK installation: pip show docker")
            raise RuntimeError(error_msg) from ex

    def _init_container(self):
        """Initialize the container."""
        if not self._client:
            raise RuntimeError("Docker client is not initialized.")

        if self.docker_path:
            self._build_docker_image()

        logger.info("Starting container for ContainerCodeExecutor...")
        run_kwargs = {}
        binds = self.host_config.get("Binds")
        if binds:
            # docker SDK `run` supports bind specs via `volumes`.
            run_kwargs["volumes"] = binds
            logger.info("Container bind mounts enabled: %s", binds)
        self._container = self._client.containers.run(
            image=self.image,
            detach=True,
            tty=True,
            **run_kwargs,
        )
        logger.info("Container %s started.", self._container.id)

        # Verify the container is able to run python3.
        self._verify_python_installation()

    def _build_docker_image(self):
        """Build the Docker image."""
        if not self.docker_path:
            raise ValueError("Docker path is not set.")
        if not os.path.exists(self.docker_path):
            raise FileNotFoundError(f"Invalid Docker path: {self.docker_path}")

        logger.info("Building Docker image...")
        self._client.images.build(
            path=self.docker_path,
            tag=self.image,
            rm=True,
        )
        logger.info("Docker image: %s built.", self.image)

    def _verify_python_installation(self):
        """Verify the container has python3 installed."""
        exec_result = self._container.exec_run(["which", "python3"])
        if exec_result.exit_code != 0:
            raise ValueError("python3 is not installed in the container.")

    def _cleanup_container(self):
        """Close the container on exit."""
        if not self._container:
            return

        logger.info("[Cleanup] Stopping the container...")
        self._container.stop()
        self._container.remove()
        logger.info("Container %s stopped and removed.", self._container.id)
        # self._container = None

    async def exec_run(self, cmd: list[str], command_args: CommandArgs) -> CommandExecResult:
        """Execute command in container."""
        timeout = command_args.timeout
        try:
            loop = asyncio.get_event_loop()
            co = loop.run_in_executor(
                None,
                lambda: self.container.exec_run(cmd=cmd[:], demux=True, environment=command_args.environment or {}))
            if command_args.timeout:
                exit_code, output = await asyncio.wait_for(co, timeout=command_args.timeout)
            else:
                exit_code, output = await co
            stdout = output[0].decode('utf-8') if output[0] else ""
            stderr = output[1].decode('utf-8') if output[1] else ""
        except asyncio.TimeoutError:
            return CommandExecResult(stdout="",
                                     stderr=f"Command timed out after {timeout}s in `{' '.join(cmd)}`\n",
                                     exit_code=-1,
                                     is_timeout=True)
        except Exception as ex:  # pylint: disable=broad-except
            return CommandExecResult(stdout="",
                                     stderr=f"Execution error: {str(ex)} in `{' '.join(cmd)}`\n",
                                     exit_code=-1,
                                     is_timeout=False)
        else:
            return CommandExecResult(stdout=stdout, stderr=stderr, exit_code=exit_code, is_timeout=False)
