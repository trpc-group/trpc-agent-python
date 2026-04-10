# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from trpc_agent_sdk.skills._state_order import marshal_loaded_order
from trpc_agent_sdk.skills._state_order import parse_loaded_order
from trpc_agent_sdk.skills._state_order import touch_loaded_order


def test_parse_loaded_order_from_json_and_bytes():
    assert parse_loaded_order('["a","b","a",""]') == ["a", "b"]
    assert parse_loaded_order(b'["x","y"]') == ["x", "y"]
    assert parse_loaded_order(b"\xff") == []


def test_marshal_loaded_order_normalizes():
    assert marshal_loaded_order(["a", "a", " ", "b"]) == '["a", "b"]'
    assert marshal_loaded_order([]) == ""


def test_touch_loaded_order_moves_items_to_tail():
    assert touch_loaded_order(["a", "b", "c"], "b", "a") == ["c", "b", "a"]
