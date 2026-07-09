# Copyright (C) 2026 Tencent. All rights reserved.
# tRPC-Agent-Python is licensed under Apache-2.0.
from __future__ import annotations

import pytest

from trpc_agent_sdk.tools.safety._safety_filter import extract_script
from trpc_agent_sdk.tools.safety._safety_filter import ToolSafetyFilter


def test_extract_script_from_code_field():
    script, lang = extract_script({"code": "eval(input())"})
    assert script == "eval(input())"


def test_extract_script_none_for_safe_args():
    assert extract_script({"city": "Beijing"}) is None


@pytest.mark.asyncio
async def test_filter_blocks_dangerous_script():
    flt = ToolSafetyFilter()
    blocked = {"called": False}

    async def handle():
        blocked["called"] = True
        return {"ok": True}

    # Simulate the filter chain: our filter wraps handle().
    from trpc_agent_sdk.tools.safety._safety_filter import _run_filter_direct
    rsp = await _run_filter_direct(flt, {"code": "exec('rm -rf /')"}, handle)
    assert blocked["called"] is False  # handle not invoked => blocked
    assert isinstance(rsp, dict) and rsp.get("error", "").startswith("TOOL_SAFETY_BLOCKED")


@pytest.mark.asyncio
async def test_filter_allows_safe_script():
    flt = ToolSafetyFilter()

    async def handle():
        return {"ok": True}

    from trpc_agent_sdk.tools.safety._safety_filter import _run_filter_direct
    rsp = await _run_filter_direct(flt, {"city": "Beijing"}, handle)
    assert rsp == {"ok": True}
