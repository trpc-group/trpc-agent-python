# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import pytest
from trpc_agent_sdk.utils import user_key


class TestUserKey:
    """Test suite for user_key function."""

    def test_user_key_basic(self):
        """Test basic user key generation."""
        result = user_key("app1", "user1")
        assert result == "app1/user1"

    def test_user_key_different_apps(self):
        """Test user key with different app names."""
        result1 = user_key("app1", "user1")
        result2 = user_key("app2", "user1")

        assert result1 == "app1/user1"
        assert result2 == "app2/user1"
        assert result1 != result2

    def test_user_key_different_users(self):
        """Test user key with different user IDs."""
        result1 = user_key("app1", "user1")
        result2 = user_key("app1", "user2")

        assert result1 == "app1/user1"
        assert result2 == "app1/user2"
        assert result1 != result2

    def test_user_key_empty_strings(self):
        """Test user key with empty strings."""
        result = user_key("", "")
        assert result == "/"

    def test_user_key_special_chars(self):
        """Test user key with special characters."""
        result = user_key("app-name", "user_id-123")
        assert result == "app-name/user_id-123"
