# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.context._constants."""

from trpc_agent_sdk.context._constants import INVOCATION_CTX


class TestInvocationCtxConstant:
    def test_value(self):
        assert INVOCATION_CTX == "invocation_ctx"

    def test_type(self):
        assert isinstance(INVOCATION_CTX, str)
