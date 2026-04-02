# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

from __future__ import annotations

import pytest

from trpc_agent_sdk.tools._long_running_tool import LongRunningFunctionTool


def long_func(query: str) -> str:
    """A long running function."""
    return f"result-{query}"


def long_func_no_doc(query: str) -> str:
    return f"result-{query}"


long_func_no_doc.__doc__ = None


class TestLongRunningFunctionToolInit:

    def test_init(self):
        tool = LongRunningFunctionTool(long_func)
        assert tool.is_long_running is True
        assert tool.name == "long_func"

    def test_init_with_invalid_filters_raises(self):
        with pytest.raises(ValueError, match="not found"):
            LongRunningFunctionTool(long_func, filters_name=["nonexistent"])


class TestLongRunningFunctionToolGetDeclaration:

    def test_declaration_appends_note(self):
        tool = LongRunningFunctionTool(long_func)
        decl = tool._get_declaration()
        assert decl is not None
        assert "long-running operation" in decl.description
        assert "A long running function." in decl.description

    def test_declaration_with_no_existing_description(self):
        tool = LongRunningFunctionTool(long_func_no_doc)
        decl = tool._get_declaration()
        assert decl is not None
        # When description is empty string '', the instruction is set with lstrip
        assert "long-running operation" in (decl.description or "")

    def test_declaration_contains_do_not_call_again(self):
        tool = LongRunningFunctionTool(long_func)
        decl = tool._get_declaration()
        assert "Do not call this tool again" in decl.description
