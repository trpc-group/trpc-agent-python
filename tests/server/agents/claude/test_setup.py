# -*- coding: utf-8 -*-
"""Unit tests for _setup module (setup_claude_env, destroy_claude_env, _add_model, _delete_model)."""

from __future__ import annotations

import base64
import os
from unittest.mock import MagicMock, Mock, patch, PropertyMock

import pytest

from trpc_agent_sdk.server.agents.claude._setup import (
    _ServerState,
    _add_model,
    _delete_model,
    _get_server_url,
    _run_server_subprocess,
    _state,
    _wait_for_server_ready,
    destroy_claude_env,
    setup_claude_env,
)


@pytest.fixture(autouse=True)
def _reset_server_state():
    """Reset global server state before each test."""
    original_process = _state.process
    original_host = _state.host
    original_port = _state.port
    original_running = _state.is_running
    _state.reset()
    yield
    _state.process = original_process
    _state.host = original_host
    _state.port = original_port
    _state.is_running = original_running


# ---------------------------------------------------------------------------
# _ServerState
# ---------------------------------------------------------------------------

class TestServerState:
    def test_initial_state(self):
        state = _ServerState()
        assert state.process is None
        assert state.host == ""
        assert state.port == 0
        assert not state.is_running

    def test_reset(self):
        state = _ServerState()
        state.process = MagicMock()
        state.host = "localhost"
        state.port = 9999
        state.is_running = True
        state.reset()
        assert state.process is None
        assert state.host == ""
        assert state.port == 0
        assert not state.is_running


# ---------------------------------------------------------------------------
# _get_server_url
# ---------------------------------------------------------------------------

class TestGetServerUrl:
    def test_raises_when_not_running(self):
        with pytest.raises(RuntimeError, match="Server not initialized"):
            _get_server_url()

    def test_returns_url_when_running(self):
        _state.is_running = True
        _state.host = "0.0.0.0"
        _state.port = 8082
        url = _get_server_url()
        assert url == "http://0.0.0.0:8082"


# ---------------------------------------------------------------------------
# _wait_for_server_ready
# ---------------------------------------------------------------------------

class TestWaitForServerReady:
    @patch("trpc_agent_sdk.server.agents.claude._setup.requests.get")
    def test_returns_true_when_server_is_ready(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp

        assert _wait_for_server_ready("localhost", 8082, timeout=2.0) is True

    @patch("trpc_agent_sdk.server.agents.claude._setup.requests.get")
    def test_returns_false_on_timeout(self, mock_get):
        import requests as req_lib
        mock_get.side_effect = req_lib.exceptions.ConnectionError("refused")

        assert _wait_for_server_ready("localhost", 8082, timeout=0.3) is False

    @patch("trpc_agent_sdk.server.agents.claude._setup.requests.get")
    def test_retries_until_ready(self, mock_get):
        import requests as req_lib
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] < 3:
                raise req_lib.exceptions.ConnectionError("not ready")
            resp = MagicMock()
            resp.status_code = 200
            return resp

        mock_get.side_effect = side_effect
        assert _wait_for_server_ready("localhost", 8082, timeout=5.0) is True
        assert call_count[0] >= 3


# ---------------------------------------------------------------------------
# _run_server_subprocess
# ---------------------------------------------------------------------------

class TestRunServerSubprocess:
    @patch("trpc_agent_sdk.server.agents.claude._setup.uvicorn.Server")
    @patch("trpc_agent_sdk.server.agents.claude._setup.uvicorn.Config")
    @patch("trpc_agent_sdk.server.agents.claude._setup.AnthropicProxyApp")
    def test_runs_without_models(self, MockApp, MockConfig, MockServer):
        mock_app_instance = MagicMock()
        mock_app_instance.app = MagicMock()
        MockApp.return_value = mock_app_instance

        mock_config = MagicMock()
        MockConfig.return_value = mock_config

        mock_server = MagicMock()
        MockServer.return_value = mock_server

        _run_server_subprocess("0.0.0.0", 8082)

        MockApp.assert_called_once_with(claude_models=None)
        MockServer.assert_called_once_with(mock_config)
        mock_server.run.assert_called_once()

    @patch("trpc_agent_sdk.server.agents.claude._setup.uvicorn.Server")
    @patch("trpc_agent_sdk.server.agents.claude._setup.uvicorn.Config")
    @patch("trpc_agent_sdk.server.agents.claude._setup.AnthropicProxyApp")
    @patch("trpc_agent_sdk.server.agents.claude._setup.pickle.loads")
    def test_runs_with_serialized_models(self, mock_loads, MockApp, MockConfig, MockServer):
        mock_models = {"sonnet": MagicMock()}
        mock_loads.return_value = mock_models

        mock_app_instance = MagicMock()
        mock_app_instance.app = MagicMock()
        MockApp.return_value = mock_app_instance
        MockServer.return_value = MagicMock()

        _run_server_subprocess("0.0.0.0", 8082, b"serialized")

        mock_loads.assert_called_once_with(b"serialized")
        MockApp.assert_called_once_with(claude_models=mock_models)


# ---------------------------------------------------------------------------
# setup_claude_env
# ---------------------------------------------------------------------------

class TestSetupClaudeEnv:
    @patch("trpc_agent_sdk.server.agents.claude._setup._wait_for_server_ready", return_value=True)
    @patch("trpc_agent_sdk.server.agents.claude._setup.multiprocessing.Process")
    def test_basic_setup(self, MockProcess, mock_wait):
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        MockProcess.return_value = mock_proc

        setup_claude_env(proxy_host="0.0.0.0", proxy_port=9999)

        assert _state.is_running
        assert _state.host == "0.0.0.0"
        assert _state.port == 9999
        assert _state.process is mock_proc
        mock_proc.start.assert_called_once()
        assert os.environ.get("ANTHROPIC_BASE_URL") == "http://0.0.0.0:9999"
        assert os.environ.get("ANTHROPIC_AUTH_TOKEN") == "xxxx"

    def test_raises_if_already_initialized(self):
        _state.process = MagicMock()
        with pytest.raises(RuntimeError, match="Server already initialized"):
            setup_claude_env()

    @patch("trpc_agent_sdk.server.agents.claude._setup._wait_for_server_ready", return_value=False)
    @patch("trpc_agent_sdk.server.agents.claude._setup.multiprocessing.Process")
    def test_raises_on_startup_timeout(self, MockProcess, mock_wait):
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.is_alive.return_value = False
        MockProcess.return_value = mock_proc

        with pytest.raises(RuntimeError, match="Server failed to start"):
            setup_claude_env(timeout=0.1)

        mock_proc.terminate.assert_called_once()
        assert _state.process is None

    @patch("trpc_agent_sdk.server.agents.claude._setup._wait_for_server_ready", return_value=False)
    @patch("trpc_agent_sdk.server.agents.claude._setup.multiprocessing.Process")
    def test_force_kills_on_timeout_if_still_alive(self, MockProcess, mock_wait):
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.is_alive.return_value = True
        MockProcess.return_value = mock_proc

        with pytest.raises(RuntimeError, match="Server failed to start"):
            setup_claude_env(timeout=0.1)

        mock_proc.kill.assert_called_once()

    @patch("trpc_agent_sdk.server.agents.claude._setup._wait_for_server_ready", return_value=True)
    @patch("trpc_agent_sdk.server.agents.claude._setup.multiprocessing.Process")
    def test_expands_all_key(self, MockProcess, mock_wait):
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        MockProcess.return_value = mock_proc

        mock_model = MagicMock()
        setup_claude_env(claude_models={"all": mock_model})

        # Verify the process was started with serialized models
        call_args = MockProcess.call_args
        assert call_args is not None

    @patch("trpc_agent_sdk.server.agents.claude._setup._wait_for_server_ready", return_value=True)
    @patch("trpc_agent_sdk.server.agents.claude._setup.multiprocessing.Process")
    def test_passes_individual_models(self, MockProcess, mock_wait):
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        MockProcess.return_value = mock_proc

        mock_model = MagicMock()
        setup_claude_env(claude_models={"sonnet": mock_model})

        assert _state.is_running


# ---------------------------------------------------------------------------
# destroy_claude_env
# ---------------------------------------------------------------------------

class TestDestroyClaudeEnv:
    def test_noop_when_not_initialized(self):
        destroy_claude_env()
        assert _state.process is None

    def test_terminates_alive_process(self):
        mock_proc = MagicMock()
        mock_proc.is_alive.return_value = True
        # After terminate + join, process is dead
        def set_dead(*args, **kwargs):
            mock_proc.is_alive.return_value = False
        mock_proc.join.side_effect = set_dead
        mock_proc.pid = 123

        _state.process = mock_proc
        _state.is_running = True

        destroy_claude_env()

        mock_proc.terminate.assert_called_once()
        assert _state.process is None
        assert not _state.is_running

    def test_force_kills_if_still_alive(self):
        mock_proc = MagicMock()
        mock_proc.is_alive.return_value = True
        mock_proc.pid = 123

        _state.process = mock_proc
        _state.is_running = True

        destroy_claude_env()

        mock_proc.terminate.assert_called_once()
        mock_proc.kill.assert_called_once()

    def test_handles_already_stopped_process(self):
        mock_proc = MagicMock()
        mock_proc.is_alive.return_value = False
        mock_proc.pid = 123

        _state.process = mock_proc
        _state.is_running = True

        destroy_claude_env()

        mock_proc.terminate.assert_not_called()
        assert _state.process is None


# ---------------------------------------------------------------------------
# _add_model
# ---------------------------------------------------------------------------

class TestAddModel:
    def test_raises_when_not_running(self):
        with pytest.raises(RuntimeError, match="Server not initialized"):
            _add_model(MagicMock())

    @patch("trpc_agent_sdk.server.agents.claude._setup.requests.post")
    def test_adds_model_successfully(self, mock_post):
        _state.is_running = True
        _state.host = "0.0.0.0"
        _state.port = 8082

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"model": "gpt4-abc123"}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        model = MagicMock()
        result = _add_model(model)

        assert result == "gpt4-abc123"
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        assert "model_data" in call_kwargs.kwargs.get("json", call_kwargs[1].get("json", {}))

    @patch("trpc_agent_sdk.server.agents.claude._setup.requests.post")
    def test_adds_model_with_config(self, mock_post):
        _state.is_running = True
        _state.host = "0.0.0.0"
        _state.port = 8082

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"model": "gpt4-abc123"}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        model = MagicMock()
        config = MagicMock()
        result = _add_model(model, config)

        assert result == "gpt4-abc123"
        call_json = mock_post.call_args.kwargs.get("json", mock_post.call_args[1].get("json", {}))
        assert "config_data" in call_json

    @patch("trpc_agent_sdk.server.agents.claude._setup.requests.post")
    def test_raises_when_no_model_key_returned(self, mock_post):
        _state.is_running = True
        _state.host = "0.0.0.0"
        _state.port = 8082

        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        with pytest.raises(RuntimeError, match="Server did not return a model key"):
            _add_model(MagicMock())


# ---------------------------------------------------------------------------
# _delete_model
# ---------------------------------------------------------------------------

class TestDeleteModel:
    def test_raises_when_not_running(self):
        with pytest.raises(RuntimeError, match="Server not initialized"):
            _delete_model("some-key")

    @patch("trpc_agent_sdk.server.agents.claude._setup.requests.post")
    def test_deletes_successfully(self, mock_post):
        _state.is_running = True
        _state.host = "0.0.0.0"
        _state.port = 8082

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"success": True, "message": "deleted"}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        result = _delete_model("model-key")
        assert result is True

    @patch("trpc_agent_sdk.server.agents.claude._setup.requests.post")
    def test_delete_returns_false_on_failure(self, mock_post):
        _state.is_running = True
        _state.host = "0.0.0.0"
        _state.port = 8082

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"success": False, "message": "not found"}
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        result = _delete_model("nonexistent-key")
        assert result is False
