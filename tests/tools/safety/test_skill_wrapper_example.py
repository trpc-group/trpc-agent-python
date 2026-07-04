import pytest

from examples.tool_safety import skill_wrapper_example as example


@pytest.fixture(autouse=True)
def clear_calls():
    example.CALLS.clear()
    yield
    example.CALLS.clear()


@pytest.mark.asyncio
async def test_skill_wrapper_allows_safe_input():
    result = await example.run_safe_python_code()

    assert result["success"] is True
    assert len(example.CALLS) == 1


@pytest.mark.asyncio
async def test_skill_wrapper_blocks_python_code_before_call():
    result = await example.run_blocked_python_code()

    assert result["error"] == "SAFETY_GUARD_BLOCKED"
    assert example.CALLS == []


@pytest.mark.asyncio
async def test_skill_wrapper_blocks_command_args_before_call():
    result = await example.run_blocked_command_args()

    assert result["error"] == "SAFETY_GUARD_BLOCKED"
    assert example.CALLS == []


@pytest.mark.asyncio
async def test_skill_wrapper_blocks_nested_payload_before_call():
    result = await example.run_blocked_nested_payload()

    assert result["error"] == "SAFETY_GUARD_BLOCKED"
    assert example.CALLS == []


@pytest.mark.asyncio
async def test_skill_wrapper_blocks_nested_python_payload_before_call():
    result = await example.run_blocked_nested_python_payload()

    assert result["error"] == "SAFETY_GUARD_BLOCKED"
    assert example.CALLS == []


@pytest.mark.asyncio
async def test_skill_wrapper_allows_nested_safe_payload():
    result = await example.run_safe_nested_payload()

    assert result["success"] is True
    assert len(example.CALLS) == 1


@pytest.mark.asyncio
async def test_skill_wrapper_blocks_mcp_like_payload_before_call():
    result = await example.run_blocked_mcp_like_payload()

    assert result["error"] == "SAFETY_GUARD_BLOCKED"
    assert example.CALLS == []
