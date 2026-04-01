# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
#
"""Since there may be multiple services in this directory, we do not import them here.
Users should explicitly import the specific classes they need from the corresponding files to avoid unnecessary imports.
"""

# Import McpServer to make it available at runtime for Pydantic model validation
# The claude_agent_sdk only imports it under TYPE_CHECKING, causing runtime errors

import claude_agent_sdk.types
from mcp.server import Server as McpServer

from ._claude_agent import ClaudeAgent
from ._proxy import AddModelRequest
from ._proxy import AddModelResponse
from ._proxy import AnthropicMessage
from ._proxy import AnthropicMessagesRequest
from ._proxy import AnthropicMessagesResponse
from ._proxy import AnthropicProxyApp
from ._proxy import AnthropicTool
from ._proxy import ContentBlockImage
from ._proxy import ContentBlockText
from ._proxy import ContentBlockToolResult
from ._proxy import ContentBlockToolUse
from ._proxy import DeleteModelRequest
from ._proxy import DeleteModelResponse
from ._proxy import SystemContent
from ._proxy import TokenCountRequest
from ._proxy import TokenCountResponse
from ._proxy import Usage
from ._proxy_logger import ProxyLogger
from ._proxy_logger import get_proxy_logger
from ._runtime import AsyncRuntime
from ._session_config import SessionConfig
from ._session_manager import SessionManager
from ._setup import destroy_claude_env
from ._setup import setup_claude_env

__all__ = [
    "McpServer",
    "ClaudeAgent",
    "AddModelRequest",
    "AddModelResponse",
    "AnthropicMessage",
    "AnthropicMessagesRequest",
    "AnthropicMessagesResponse",
    "AnthropicProxyApp",
    "AnthropicTool",
    "ContentBlockImage",
    "ContentBlockText",
    "ContentBlockToolResult",
    "ContentBlockToolUse",
    "DeleteModelRequest",
    "DeleteModelResponse",
    "SystemContent",
    "TokenCountRequest",
    "TokenCountResponse",
    "Usage",
    "ProxyLogger",
    "get_proxy_logger",
    "AsyncRuntime",
    "SessionConfig",
    "SessionManager",
    "destroy_claude_env",
    "setup_claude_env",
]

# Inject McpServer into claude_agent_sdk.types namespace so Pydantic can resolve it
claude_agent_sdk.types.McpServer = McpServer
# Rebuild the ClaudeAgent model with McpServer now available
ClaudeAgent.model_rebuild()
