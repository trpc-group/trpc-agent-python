# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for container CLI module (_container_cli.py).

Covers:
- ContainerConfig dataclass defaults and custom values
- CommandArgs dataclass defaults and custom values
- ContainerClient initialization (Docker client, container, image build)
- ContainerClient.exec_run async method (success, timeout, exception)
- Cleanup, verification, and error-handling branches
"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import MagicMock, Mock, patch

import pytest

from trpc_agent_sdk.code_executors.container._container_cli import (
    DEFAULT_IMAGE_TAG,
    CommandArgs,
    ContainerClient,
    ContainerConfig,
)
from trpc_agent_sdk.utils import CommandExecResult


# ---------------------------------------------------------------------------
# ContainerConfig
# ---------------------------------------------------------------------------


class TestContainerConfig:

    def test_defaults(self):
        cfg = ContainerConfig()
        assert cfg.base_url is None
        assert cfg.image == DEFAULT_IMAGE_TAG
        assert cfg.docker_path is None
        assert cfg.host_config is None

    def test_custom_values(self):
        cfg = ContainerConfig(
            base_url="tcp://localhost:2375",
            image="my-image:latest",
            docker_path="/path/to/docker",
            host_config={"Binds": ["/host:/container"]},
        )
        assert cfg.base_url == "tcp://localhost:2375"
        assert cfg.image == "my-image:latest"
        assert cfg.docker_path == "/path/to/docker"
        assert cfg.host_config == {"Binds": ["/host:/container"]}


# ---------------------------------------------------------------------------
# CommandArgs
# ---------------------------------------------------------------------------


class TestCommandArgs:

    def test_defaults(self):
        args = CommandArgs()
        assert args.environment is None
        assert args.timeout is None

    def test_custom_values(self):
        args = CommandArgs(environment={"KEY": "VAL"}, timeout=30.0)
        assert args.environment == {"KEY": "VAL"}
        assert args.timeout == 30.0


# ---------------------------------------------------------------------------
# Helpers for mocking Docker SDK objects
# ---------------------------------------------------------------------------


def _make_mock_docker_client(ping_ok=True):
    """Return a mock docker.DockerClient with controllable ping."""
    client = MagicMock()
    if ping_ok:
        client.ping.return_value = True
    else:
        import docker
        client.ping.side_effect = docker.errors.DockerException("ping failed")
    return client


def _make_mock_container(exec_exit_code=0):
    """Return a mock Container with controllable exec_run."""
    container = MagicMock()
    container.id = "abc123"
    container.exec_run.return_value = MagicMock(exit_code=exec_exit_code)
    return container


# ---------------------------------------------------------------------------
# ContainerClient.__init__ / _init_docker_client
# ---------------------------------------------------------------------------


class TestContainerClientInitDockerClient:

    @patch("trpc_agent_sdk.code_executors.container._container_cli.atexit")
    @patch("trpc_agent_sdk.code_executors.container._container_cli.docker")
    def test_from_env_when_no_base_url(self, mock_docker, mock_atexit):
        mock_client = _make_mock_docker_client()
        mock_docker.from_env.return_value = mock_client
        mock_docker.errors.DockerException = Exception

        mock_container = _make_mock_container()
        mock_client.containers.run.return_value = mock_container

        cc = ContainerClient(config=ContainerConfig())

        mock_docker.from_env.assert_called_once()
        mock_client.ping.assert_called_once()
        assert cc.client is mock_client

    @patch("trpc_agent_sdk.code_executors.container._container_cli.atexit")
    @patch("trpc_agent_sdk.code_executors.container._container_cli.docker")
    def test_custom_base_url(self, mock_docker, mock_atexit):
        mock_client = _make_mock_docker_client()
        mock_docker.DockerClient.return_value = mock_client
        mock_docker.errors.DockerException = Exception

        mock_container = _make_mock_container()
        mock_client.containers.run.return_value = mock_container

        cc = ContainerClient(config=ContainerConfig(base_url="tcp://remote:2375"))

        mock_docker.DockerClient.assert_called_once_with(base_url="tcp://remote:2375")
        assert cc.base_url == "tcp://remote:2375"

    @patch("trpc_agent_sdk.code_executors.container._container_cli.atexit")
    @patch("trpc_agent_sdk.code_executors.container._container_cli.docker")
    def test_docker_exception_connection_error(self, mock_docker, mock_atexit):
        exc_cls = type("DockerException", (Exception,), {})
        mock_docker.errors.DockerException = exc_cls
        mock_docker.from_env.side_effect = exc_cls("Connection refused")

        with pytest.raises(RuntimeError, match="Failed to connect to Docker daemon"):
            ContainerClient(config=ContainerConfig())

    @patch("trpc_agent_sdk.code_executors.container._container_cli.atexit")
    @patch("trpc_agent_sdk.code_executors.container._container_cli.docker")
    def test_docker_exception_socket_error(self, mock_docker, mock_atexit):
        exc_cls = type("DockerException", (Exception,), {})
        mock_docker.errors.DockerException = exc_cls
        mock_docker.from_env.side_effect = exc_cls("No such file or directory")

        with pytest.raises(RuntimeError, match="Failed to connect to Docker daemon"):
            ContainerClient(config=ContainerConfig())

    @patch("trpc_agent_sdk.code_executors.container._container_cli.atexit")
    @patch("trpc_agent_sdk.code_executors.container._container_cli.docker")
    def test_docker_exception_generic(self, mock_docker, mock_atexit):
        exc_cls = type("DockerException", (Exception,), {})
        mock_docker.errors.DockerException = exc_cls
        mock_docker.from_env.side_effect = exc_cls("some other error")

        with pytest.raises(RuntimeError, match="Failed to connect to Docker daemon: some other error"):
            ContainerClient(config=ContainerConfig())

    @patch("trpc_agent_sdk.code_executors.container._container_cli.atexit")
    @patch("trpc_agent_sdk.code_executors.container._container_cli.docker")
    def test_unexpected_exception(self, mock_docker, mock_atexit):
        mock_docker.errors.DockerException = type("DockerException", (Exception,), {})
        mock_docker.from_env.side_effect = OSError("unexpected")

        with pytest.raises(RuntimeError, match="Unexpected error initializing Docker client"):
            ContainerClient(config=ContainerConfig())


# ---------------------------------------------------------------------------
# ContainerClient._init_container
# ---------------------------------------------------------------------------


class TestContainerClientInitContainer:

    @patch("trpc_agent_sdk.code_executors.container._container_cli.atexit")
    @patch("trpc_agent_sdk.code_executors.container._container_cli.docker")
    def test_container_starts_and_verifies_python(self, mock_docker, mock_atexit):
        mock_client = _make_mock_docker_client()
        mock_docker.from_env.return_value = mock_client
        mock_docker.errors.DockerException = Exception

        mock_container = _make_mock_container(exec_exit_code=0)
        mock_client.containers.run.return_value = mock_container

        cc = ContainerClient(config=ContainerConfig())

        mock_client.containers.run.assert_called_once_with(
            image=DEFAULT_IMAGE_TAG, detach=True, tty=True)
        mock_container.exec_run.assert_called_once_with(["which", "python3"])
        assert cc.container is mock_container

    @patch("trpc_agent_sdk.code_executors.container._container_cli.atexit")
    @patch("trpc_agent_sdk.code_executors.container._container_cli.docker")
    def test_container_with_bind_mounts(self, mock_docker, mock_atexit):
        mock_client = _make_mock_docker_client()
        mock_docker.from_env.return_value = mock_client
        mock_docker.errors.DockerException = Exception

        mock_container = _make_mock_container()
        mock_client.containers.run.return_value = mock_container

        cfg = ContainerConfig(host_config={"Binds": ["/host/skills:/opt/skills:ro"]})
        cc = ContainerClient(config=cfg)

        mock_client.containers.run.assert_called_once_with(
            image=DEFAULT_IMAGE_TAG, detach=True, tty=True,
            volumes=["/host/skills:/opt/skills:ro"])

    @patch("trpc_agent_sdk.code_executors.container._container_cli.atexit")
    @patch("trpc_agent_sdk.code_executors.container._container_cli.docker")
    def test_python3_not_installed_raises(self, mock_docker, mock_atexit):
        mock_client = _make_mock_docker_client()
        mock_docker.from_env.return_value = mock_client
        mock_docker.errors.DockerException = Exception

        mock_container = _make_mock_container(exec_exit_code=1)
        mock_client.containers.run.return_value = mock_container

        with pytest.raises(ValueError, match="python3 is not installed"):
            ContainerClient(config=ContainerConfig())

    @patch("trpc_agent_sdk.code_executors.container._container_cli.atexit")
    @patch("trpc_agent_sdk.code_executors.container._container_cli.docker")
    def test_init_container_client_not_initialized(self, mock_docker, mock_atexit):
        mock_client = _make_mock_docker_client()
        mock_docker.from_env.return_value = mock_client
        mock_docker.errors.DockerException = Exception

        cc = ContainerClient.__new__(ContainerClient)
        cc._client = None
        cc._container = None
        cc.docker_path = None
        cc.host_config = {}
        cc.image = DEFAULT_IMAGE_TAG
        cc.base_url = None

        with pytest.raises(RuntimeError, match="Docker client is not initialized"):
            cc._init_container()


# ---------------------------------------------------------------------------
# ContainerClient._build_docker_image
# ---------------------------------------------------------------------------


class TestContainerClientBuildDockerImage:

    @patch("trpc_agent_sdk.code_executors.container._container_cli.atexit")
    @patch("trpc_agent_sdk.code_executors.container._container_cli.docker")
    def test_build_image_success(self, mock_docker, mock_atexit, tmp_path):
        mock_client = _make_mock_docker_client()
        mock_docker.from_env.return_value = mock_client
        mock_docker.errors.DockerException = Exception

        mock_container = _make_mock_container()
        mock_client.containers.run.return_value = mock_container

        docker_dir = str(tmp_path)
        cfg = ContainerConfig(docker_path=docker_dir, image="custom:latest")
        cc = ContainerClient(config=cfg)

        mock_client.images.build.assert_called_once_with(
            path=os.path.abspath(docker_dir), tag="custom:latest", rm=True)

    @patch("trpc_agent_sdk.code_executors.container._container_cli.atexit")
    @patch("trpc_agent_sdk.code_executors.container._container_cli.docker")
    def test_build_image_path_not_exists(self, mock_docker, mock_atexit):
        mock_client = _make_mock_docker_client()
        mock_docker.from_env.return_value = mock_client
        mock_docker.errors.DockerException = Exception

        with pytest.raises(FileNotFoundError, match="Invalid Docker path"):
            ContainerClient(config=ContainerConfig(
                docker_path="/nonexistent/docker/path", image="custom:latest"))

    def test_build_image_no_docker_path(self):
        cc = ContainerClient.__new__(ContainerClient)
        cc.docker_path = None
        cc._client = MagicMock()

        with pytest.raises(ValueError, match="Docker path is not set"):
            cc._build_docker_image()


# ---------------------------------------------------------------------------
# ContainerClient._cleanup_container
# ---------------------------------------------------------------------------


class TestContainerClientCleanup:

    def test_cleanup_with_container(self):
        cc = ContainerClient.__new__(ContainerClient)
        cc._container = MagicMock()

        cc._cleanup_container()

        cc._container.stop.assert_called_once()
        cc._container.remove.assert_called_once()

    def test_cleanup_without_container(self):
        cc = ContainerClient.__new__(ContainerClient)
        cc._container = None

        cc._cleanup_container()


# ---------------------------------------------------------------------------
# ContainerClient.exec_run
# ---------------------------------------------------------------------------


class TestContainerClientExecRun:

    async def test_exec_run_success_no_timeout(self):
        cc = ContainerClient.__new__(ContainerClient)
        mock_container = MagicMock()
        mock_container.exec_run.return_value = (0, (b"hello", b""))
        cc._container = mock_container

        args = CommandArgs(environment={"KEY": "VAL"}, timeout=None)
        result = await cc.exec_run(cmd=["echo", "hello"], command_args=args)

        assert isinstance(result, CommandExecResult)
        assert result.stdout == "hello"
        assert result.stderr == ""
        assert result.exit_code == 0
        assert result.is_timeout is False

    async def test_exec_run_success_with_timeout(self):
        cc = ContainerClient.__new__(ContainerClient)
        mock_container = MagicMock()
        mock_container.exec_run.return_value = (0, (b"output", b"warn"))
        cc._container = mock_container

        args = CommandArgs(environment=None, timeout=10.0)
        result = await cc.exec_run(cmd=["python3", "-c", "print('output')"], command_args=args)

        assert result.stdout == "output"
        assert result.stderr == "warn"
        assert result.exit_code == 0
        assert result.is_timeout is False

    async def test_exec_run_with_none_stdout_stderr(self):
        cc = ContainerClient.__new__(ContainerClient)
        mock_container = MagicMock()
        mock_container.exec_run.return_value = (0, (None, None))
        cc._container = mock_container

        args = CommandArgs()
        result = await cc.exec_run(cmd=["true"], command_args=args)

        assert result.stdout == ""
        assert result.stderr == ""

    async def test_exec_run_timeout_error(self):
        cc = ContainerClient.__new__(ContainerClient)
        mock_container = MagicMock()

        async def _slow_exec(*args, **kwargs):
            await asyncio.sleep(10)

        mock_container.exec_run.side_effect = lambda **kw: asyncio.sleep(100)
        cc._container = mock_container

        args = CommandArgs(timeout=0.01)

        loop = asyncio.get_event_loop()
        original_run_in_executor = loop.run_in_executor

        async def mock_run_in_executor(executor, func):
            await asyncio.sleep(10)

        with patch.object(loop, 'run_in_executor', side_effect=mock_run_in_executor):
            result = await cc.exec_run(cmd=["sleep", "100"], command_args=args)

        assert result.exit_code == -1
        assert result.is_timeout is True
        assert "timed out" in result.stderr

    async def test_exec_run_generic_exception(self):
        cc = ContainerClient.__new__(ContainerClient)
        mock_container = MagicMock()
        mock_container.exec_run.side_effect = RuntimeError("Docker API error")
        cc._container = mock_container

        args = CommandArgs(timeout=None)

        loop = asyncio.get_event_loop()

        async def mock_run_in_executor(executor, func):
            return func()

        with patch.object(loop, 'run_in_executor', side_effect=mock_run_in_executor):
            result = await cc.exec_run(cmd=["bad", "cmd"], command_args=args)

        assert result.exit_code == -1
        assert result.is_timeout is False
        assert "Execution error" in result.stderr
        assert "Docker API error" in result.stderr

    async def test_exec_run_nonzero_exit_code(self):
        cc = ContainerClient.__new__(ContainerClient)
        mock_container = MagicMock()
        mock_container.exec_run.return_value = (1, (b"", b"error output"))
        cc._container = mock_container

        args = CommandArgs()
        result = await cc.exec_run(cmd=["false"], command_args=args)

        assert result.exit_code == 1
        assert result.stderr == "error output"
        assert result.is_timeout is False


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


class TestContainerClientProperties:

    def test_client_property(self):
        cc = ContainerClient.__new__(ContainerClient)
        cc._client = MagicMock()
        assert cc.client is cc._client

    def test_container_property(self):
        cc = ContainerClient.__new__(ContainerClient)
        cc._container = MagicMock()
        assert cc.container is cc._container


# ---------------------------------------------------------------------------
# DEFAULT_IMAGE_TAG
# ---------------------------------------------------------------------------


class TestConstants:

    def test_default_image_tag(self):
        assert DEFAULT_IMAGE_TAG == "python:3-slim"
