from unittest.mock import AsyncMock
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from trpc_agent_sdk.code_executors._types import CodeBlock
from trpc_agent_sdk.code_executors._types import CodeExecutionInput
from trpc_agent_sdk.code_executors.local import UnsafeLocalCodeExecutor
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools import BashTool


@pytest.fixture
def tool_context():
    return Mock(spec=InvocationContext)


@pytest.mark.asyncio
async def test_bash_tool_default_preserves_existing_behavior(tmp_path, tool_context):
    tool = BashTool(cwd=str(tmp_path))
    with patch("trpc_agent_sdk.tools.file_tools._bash_tool.ToolScriptSafetyScanner") as scanner_cls:
        result = await tool._run_async_impl(tool_context=tool_context, args={"command": "echo ok"})
    scanner_cls.assert_not_called()
    assert result["success"] is True


@pytest.mark.asyncio
async def test_bash_tool_safety_blocks_before_subprocess(tmp_path, tool_context):
    tool = BashTool(cwd=str(tmp_path), enable_safety_guard=True)
    with patch("trpc_agent_sdk.tools.file_tools._bash_tool.asyncio.create_subprocess_shell", new=AsyncMock()) as proc:
        result = await tool._run_async_impl(tool_context=tool_context, args={"command": "rm -rf /"})
    proc.assert_not_called()
    assert result["error"] == "SAFETY_GUARD_BLOCKED"


@pytest.mark.asyncio
async def test_bash_tool_safety_allows_safe_command(tmp_path, tool_context):
    tool = BashTool(cwd=str(tmp_path), enable_safety_guard=True)
    result = await tool._run_async_impl(tool_context=tool_context, args={"command": "echo ok"})
    assert result["success"] is True
    assert "ok" in result["stdout"]


@pytest.mark.asyncio
async def test_unsafe_executor_blocks_dangerous_code_before_execute(tmp_path):
    executor = UnsafeLocalCodeExecutor(enable_safety_guard=True, work_dir=str(tmp_path))
    input_data = CodeExecutionInput(code_blocks=[CodeBlock(language="python", code='open(".env").read()')])
    with patch.object(executor, "_execute_code_block", new=AsyncMock()) as execute:
        result = await executor.execute_code(Mock(spec=InvocationContext), input_data)
    execute.assert_not_called()
    assert "SAFETY_GUARD_BLOCKED" in result.output


@pytest.mark.asyncio
async def test_unsafe_executor_default_behavior_unchanged(tmp_path):
    executor = UnsafeLocalCodeExecutor(work_dir=str(tmp_path))
    input_data = CodeExecutionInput(code_blocks=[CodeBlock(language="python", code="print('ok')")])
    with patch.object(executor, "_execute_code_block", new=AsyncMock(return_value="ok")) as execute:
        result = await executor.execute_code(Mock(spec=InvocationContext), input_data)
    execute.assert_called_once()
    assert "ok" in result.output
