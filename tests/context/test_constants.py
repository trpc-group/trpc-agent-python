# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for trpc_agent_sdk.context._constants."""

from trpc_agent_sdk.context._constants import INVOCATION_CTX


class TestInvocationCtxConstant:
    def test_value(self):
        assert INVOCATION_CTX == "invocation_ctx"

    def test_type(self):
        assert isinstance(INVOCATION_CTX, str)
