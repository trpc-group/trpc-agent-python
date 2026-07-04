import pytest

from trpc_agent_sdk.tools.safety import with_tool_safety


def test_supports_sync_callable():
    wrapped = with_tool_safety(lambda command: {"success": True, "command": command}, language="bash")
    assert wrapped("echo ok")["success"] is True


@pytest.mark.asyncio
async def test_supports_async_callable():
    async def target(command):
        return {"success": True, "command": command}

    wrapped = with_tool_safety(target, language="bash")
    result = await wrapped("echo ok")
    assert result["success"] is True


def test_deny_prevents_target_call():
    called = False

    def target(command):
        nonlocal called
        called = True
        return {"success": True, "command": command}

    wrapped = with_tool_safety(target, language="bash")
    result = wrapped("rm -rf /")
    assert not called
    assert result["error"] == "SAFETY_GUARD_BLOCKED"
