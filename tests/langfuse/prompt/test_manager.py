# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.server.langfuse.prompt._manager.

Covers:
- RemoteInstructionManager initialisation
- RemoteInstructionManager.get_instruction (success, version/label params, URL encoding, errors)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from trpc_agent_sdk.server.langfuse.prompt._manager import RemoteInstructionManager
from trpc_agent_sdk.types import Instruction, InstructionMetadata


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_RESPONSE = {
    "name": "greet",
    "version": 3,
    "type": "text",
    "labels": ["production"],
    "config": {"temperature": 0.5},
    "prompt": "Hello, {{name}}!",
}


@pytest.fixture
def manager():
    return RemoteInstructionManager(
        public_key="pk-test",
        secret_key="sk-test",
        host="https://langfuse.example.com/",
    )


def _mock_response(json_data=None, status_code=200, raise_for_status=None):
    """Build a mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or SAMPLE_RESPONSE
    if raise_for_status:
        resp.raise_for_status.side_effect = raise_for_status
    else:
        resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# RemoteInstructionManager.__init__
# ---------------------------------------------------------------------------
class TestRemoteInstructionManagerInit:
    """Tests for __init__."""

    def test_host_trailing_slash_stripped(self):
        mgr = RemoteInstructionManager("pk", "sk", "https://host.com/")
        assert mgr._host == "https://host.com"

    def test_host_no_trailing_slash(self):
        mgr = RemoteInstructionManager("pk", "sk", "https://host.com")
        assert mgr._host == "https://host.com"

    def test_auth_tuple_stored(self):
        mgr = RemoteInstructionManager("pk", "sk", "https://host.com")
        assert mgr._auth == ("pk", "sk")


# ---------------------------------------------------------------------------
# RemoteInstructionManager.get_instruction — success cases
# ---------------------------------------------------------------------------
class TestGetInstructionSuccess:
    """Happy-path tests for get_instruction."""

    @patch("trpc_agent_sdk.server.langfuse.prompt._manager.requests.get")
    def test_basic_fetch(self, mock_get, manager):
        mock_get.return_value = _mock_response()
        result = manager.get_instruction("greet")

        assert isinstance(result, Instruction)
        assert result.instruction == "Hello, {{name}}!"
        assert isinstance(result.metadata, InstructionMetadata)
        assert result.metadata.name == "greet"
        assert result.metadata.version == 3

    @patch("trpc_agent_sdk.server.langfuse.prompt._manager.requests.get")
    def test_url_construction_no_params(self, mock_get, manager):
        mock_get.return_value = _mock_response()
        manager.get_instruction("greet")

        mock_get.assert_called_once()
        args, kwargs = mock_get.call_args
        assert args[0] == "https://langfuse.example.com/api/public/v2/prompts/greet"
        assert kwargs["params"] == {}
        assert kwargs["auth"] == ("pk-test", "sk-test")
        assert kwargs["timeout"] == 10

    @patch("trpc_agent_sdk.server.langfuse.prompt._manager.requests.get")
    def test_url_encodes_special_characters(self, mock_get, manager):
        mock_get.return_value = _mock_response()
        manager.get_instruction("my prompt/name")

        url = mock_get.call_args[0][0]
        assert "my%20prompt%2Fname" in url

    @patch("trpc_agent_sdk.server.langfuse.prompt._manager.requests.get")
    def test_with_version_param(self, mock_get, manager):
        mock_get.return_value = _mock_response()
        manager.get_instruction("greet", version=5)

        params = mock_get.call_args[1]["params"]
        assert params == {"version": 5}

    @patch("trpc_agent_sdk.server.langfuse.prompt._manager.requests.get")
    def test_with_label_param(self, mock_get, manager):
        mock_get.return_value = _mock_response()
        manager.get_instruction("greet", label="staging")

        params = mock_get.call_args[1]["params"]
        assert params == {"label": "staging"}

    @patch("trpc_agent_sdk.server.langfuse.prompt._manager.requests.get")
    def test_with_version_and_label(self, mock_get, manager):
        mock_get.return_value = _mock_response()
        manager.get_instruction("greet", version=2, label="production")

        params = mock_get.call_args[1]["params"]
        assert params == {"version": 2, "label": "production"}

    @patch("trpc_agent_sdk.server.langfuse.prompt._manager.requests.get")
    def test_metadata_fields(self, mock_get, manager):
        mock_get.return_value = _mock_response()
        result = manager.get_instruction("greet")

        assert result.metadata.type == "text"
        assert result.metadata.labels == ["production"]
        assert result.metadata.config == {"temperature": 0.5}

    @patch("trpc_agent_sdk.server.langfuse.prompt._manager.requests.get")
    def test_metadata_defaults_for_missing_fields(self, mock_get, manager):
        data = {"name": "simple", "version": 1, "prompt": "hi"}
        mock_get.return_value = _mock_response(json_data=data)
        result = manager.get_instruction("simple")

        assert result.metadata.type == "text"
        assert result.metadata.labels == []
        assert result.metadata.config == {}

    @patch("trpc_agent_sdk.server.langfuse.prompt._manager.requests.get")
    def test_chat_type_instruction(self, mock_get, manager):
        data = {
            "name": "chat_prompt",
            "version": 1,
            "type": "chat",
            "labels": ["staging"],
            "config": {},
            "prompt": '[{"role": "system", "content": "You are helpful."}]',
        }
        mock_get.return_value = _mock_response(json_data=data)
        result = manager.get_instruction("chat_prompt")

        assert result.metadata.type == "chat"
        assert result.instruction == '[{"role": "system", "content": "You are helpful."}]'


# ---------------------------------------------------------------------------
# RemoteInstructionManager.get_instruction — error cases
# ---------------------------------------------------------------------------
class TestGetInstructionErrors:
    """Error-handling tests for get_instruction."""

    @patch("trpc_agent_sdk.server.langfuse.prompt._manager.requests.get")
    def test_http_error_raises(self, mock_get, manager):
        from requests.exceptions import HTTPError

        mock_get.return_value = _mock_response(
            raise_for_status=HTTPError("404 Not Found"),
        )
        with pytest.raises(HTTPError, match="404"):
            manager.get_instruction("missing")

    @patch("trpc_agent_sdk.server.langfuse.prompt._manager.requests.get")
    def test_connection_error_raises(self, mock_get, manager):
        from requests.exceptions import ConnectionError as ReqConnectionError

        mock_get.side_effect = ReqConnectionError("Connection refused")
        with pytest.raises(ReqConnectionError):
            manager.get_instruction("greet")

    @patch("trpc_agent_sdk.server.langfuse.prompt._manager.requests.get")
    def test_timeout_error_raises(self, mock_get, manager):
        from requests.exceptions import Timeout

        mock_get.side_effect = Timeout("Request timed out")
        with pytest.raises(Timeout):
            manager.get_instruction("greet")

    @patch("trpc_agent_sdk.server.langfuse.prompt._manager.requests.get")
    def test_generic_exception_propagates(self, mock_get, manager):
        mock_get.side_effect = RuntimeError("unexpected")
        with pytest.raises(RuntimeError, match="unexpected"):
            manager.get_instruction("greet")
