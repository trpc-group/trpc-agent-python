from unittest.mock import Mock

import pytest

from trpc_agent_sdk.tools.safety import ToolSafetyFilter


@pytest.mark.asyncio
async def test_allow_case_calls_handler():
    safety_filter = ToolSafetyFilter()
    called = False

    async def handle():
        nonlocal called
        called = True
        return {"success": True}

    result = await safety_filter.run(Mock(), {"command": "echo ok"}, handle)
    assert called
    assert result.rsp == {"success": True}


@pytest.mark.asyncio
async def test_deny_case_does_not_call_handler():
    safety_filter = ToolSafetyFilter()
    called = False

    async def handle():
        nonlocal called
        called = True
        return {"success": True}

    result = await safety_filter.run(Mock(), {"command": "rm -rf /"}, handle)
    assert not called
    assert result.rsp["error"] == "SAFETY_GUARD_BLOCKED"


@pytest.mark.asyncio
async def test_blocked_response_has_report():
    result = await ToolSafetyFilter().run(Mock(), {"command": "cat .env"}, lambda: None)
    assert result.rsp["error"] == "SAFETY_GUARD_BLOCKED"
    assert result.rsp["safety_report"]["decision"] == "deny"


@pytest.mark.asyncio
async def test_needs_human_review_not_blocked_by_default():
    called = False

    async def handle():
        nonlocal called
        called = True
        return "ok"

    result = await ToolSafetyFilter().run(Mock(), {"command": "echo hi | cat"}, handle)
    assert called
    assert result.rsp == "ok"


@pytest.mark.asyncio
async def test_needs_human_review_blocked_when_enabled():
    called = False

    async def handle():
        nonlocal called
        called = True
        return "ok"

    result = await ToolSafetyFilter(block_on_review=True).run(Mock(), {"command": "echo hi | cat"}, handle)
    assert not called
    assert result.rsp["error"] == "SAFETY_GUARD_BLOCKED"
