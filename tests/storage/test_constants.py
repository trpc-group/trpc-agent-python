# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for storage constants."""

from trpc_agent_sdk.storage._constants import DEFAULT_MAX_KEY_LENGTH
from trpc_agent_sdk.storage._constants import DEFAULT_MAX_VARCHAR_LENGTH


class TestConstants:

    def test_default_max_key_length_value(self):
        assert DEFAULT_MAX_KEY_LENGTH == 128

    def test_default_max_varchar_length_value(self):
        assert DEFAULT_MAX_VARCHAR_LENGTH == 256

    def test_constants_are_int(self):
        assert isinstance(DEFAULT_MAX_KEY_LENGTH, int)
        assert isinstance(DEFAULT_MAX_VARCHAR_LENGTH, int)


class TestConstantsReexport:

    def test_reexported_from_package(self):
        from trpc_agent_sdk.storage import DEFAULT_MAX_KEY_LENGTH as pkg_key_len
        from trpc_agent_sdk.storage import DEFAULT_MAX_VARCHAR_LENGTH as pkg_varchar_len

        assert pkg_key_len == DEFAULT_MAX_KEY_LENGTH
        assert pkg_varchar_len == DEFAULT_MAX_VARCHAR_LENGTH
