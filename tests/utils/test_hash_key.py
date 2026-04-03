# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.utils._hash_key.

Covers:
- user_key: basic generation, edge cases, special characters, uniqueness
"""

from trpc_agent_sdk.utils import user_key


class TestUserKey:
    """Test suite for user_key function."""

    def test_basic(self):
        assert user_key("app1", "user1") == "app1/user1"

    def test_different_apps(self):
        r1 = user_key("app1", "user1")
        r2 = user_key("app2", "user1")
        assert r1 != r2
        assert r1 == "app1/user1"
        assert r2 == "app2/user1"

    def test_different_users(self):
        r1 = user_key("app1", "user1")
        r2 = user_key("app1", "user2")
        assert r1 != r2

    def test_empty_strings(self):
        assert user_key("", "") == "/"

    def test_special_chars(self):
        assert user_key("app-name", "user_id-123") == "app-name/user_id-123"

    def test_unicode(self):
        assert user_key("应用", "用户") == "应用/用户"

    def test_with_spaces(self):
        assert user_key("my app", "my user") == "my app/my user"

    def test_return_type(self):
        assert isinstance(user_key("a", "b"), str)

    def test_format_consistency(self):
        result = user_key("x", "y")
        parts = result.split("/")
        assert len(parts) == 2
        assert parts[0] == "x"
        assert parts[1] == "y"
