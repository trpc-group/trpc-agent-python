# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
#
# Below code are copy and modified from https://github.com/ag-ui-protocol/ag-ui.git
#
# MIT License
#
# Copyright (c) 2025
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
"""TRPC Agent Middleware for AG-UI Protocol

This middleware enables TRPC agents to be used with the AG-UI protocol.
"""

from ._agui_agent import AgUiAgent
from ._client_proxy_tool import ClientProxyTool
from ._client_proxy_toolset import ClientProxyToolset
from ._converters import convert_ag_ui_messages_to_trpc
from ._converters import convert_json_patch_to_state
from ._converters import convert_message_content_to_parts
from ._converters import convert_state_to_json_patch
from ._converters import convert_trpc_event_to_ag_ui_message
from ._converters import create_error_message
from ._converters import extract_text_from_content
from ._endpoint import add_trpc_fastapi_endpoint
from ._endpoint import create_trpc_app
from ._event_translator import EventTranslator
from ._execution_state import ExecutionState
from ._feed_back_content import AgUiUserFeedBack
from ._http_req import get_agui_http_req
from ._http_req import set_agui_http_req
from ._session_manager import SessionManager

__all__ = [
    "AgUiAgent",
    "ClientProxyTool",
    "ClientProxyToolset",
    "convert_ag_ui_messages_to_trpc",
    "convert_json_patch_to_state",
    "convert_message_content_to_parts",
    "convert_state_to_json_patch",
    "convert_trpc_event_to_ag_ui_message",
    "create_error_message",
    "extract_text_from_content",
    "add_trpc_fastapi_endpoint",
    "create_trpc_app",
    "EventTranslator",
    "ExecutionState",
    "AgUiUserFeedBack",
    "get_agui_http_req",
    "set_agui_http_req",
    "SessionManager",
]
