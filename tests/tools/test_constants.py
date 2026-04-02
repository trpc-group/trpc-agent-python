# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

from trpc_agent_sdk.tools._constants import (
    DEFAULT_API_VARIANT,
    DEFAULT_TOOLSET_NAME,
    INPUT_STREAM,
    TOOL_CONTEXT,
    TOOL_NAME,
)


class TestConstants:

    def test_tool_context_value(self):
        assert TOOL_CONTEXT == "tool_context"

    def test_input_stream_value(self):
        assert INPUT_STREAM == "input_stream"

    def test_default_api_variant_value(self):
        assert DEFAULT_API_VARIANT == "default"

    def test_default_toolset_name_value(self):
        assert DEFAULT_TOOLSET_NAME == "default"

    def test_tool_name_value(self):
        assert TOOL_NAME == "set_model_response"

    def test_constants_are_strings(self):
        for const in [TOOL_CONTEXT, INPUT_STREAM, DEFAULT_API_VARIANT, DEFAULT_TOOLSET_NAME, TOOL_NAME]:
            assert isinstance(const, str)
