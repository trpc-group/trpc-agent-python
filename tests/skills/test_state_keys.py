# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from trpc_agent_sdk.skills._state_keys import docs_key
from trpc_agent_sdk.skills._state_keys import docs_prefix
from trpc_agent_sdk.skills._state_keys import loaded_key
from trpc_agent_sdk.skills._state_keys import loaded_order_key
from trpc_agent_sdk.skills._state_keys import loaded_prefix
from trpc_agent_sdk.skills._state_keys import to_persistent_prefix
from trpc_agent_sdk.skills._state_keys import tool_key
from trpc_agent_sdk.skills._state_keys import tool_prefix


def test_loaded_key_legacy_fallback():
    assert loaded_key("", "demo") == "temp:skill:loaded:demo"


def test_scoped_keys_escape_agent_name():
    assert loaded_key("agent/a", "demo") == "temp:skill:loaded_by_agent:agent%2Fa/demo"
    assert docs_key("agent/a", "demo") == "temp:skill:docs_by_agent:agent%2Fa/demo"
    assert tool_key("agent/a", "demo") == "temp:skill:tools_by_agent:agent%2Fa/demo"


def test_prefix_helpers():
    assert loaded_prefix("") == "temp:skill:loaded:"
    assert docs_prefix("") == "temp:skill:docs:"
    assert tool_prefix("") == "temp:skill:tools:"
    assert loaded_order_key("agent/a") == "temp:skill:loaded_order_by_agent:agent%2Fa"


def test_to_persistent_prefix():
    assert to_persistent_prefix("temp:skill:loaded:demo") == "skill:loaded:demo"
