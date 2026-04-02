# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""
Comprehensive Streaming Tool Test Demo

This example tests all streaming tool scenarios to verify:
1. Stream tool call event propagation
2. Sync/Async function -> StreamingFunctionTool conversion
3. FunctionTool -> StreamingFunctionTool conversion
4. ToolSet containing streaming tools
5. Custom BaseTool with is_streaming=True
6. Mixed configuration: ToolSet + FunctionTool + StreamingFunctionTool

Test Results:
- Streaming tools should emit streaming events (is_streaming_tool_call() = True)
- Non-streaming tools should NOT emit streaming events
- All tool types should execute correctly
"""

import asyncio
import uuid
from collections import defaultdict

from dotenv import load_dotenv
from trpc_agent_sdk.models import TOOL_STREAMING_ARGS
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()

# Track streaming events per tool for verification
streaming_event_count = defaultdict(int)
tool_execution_count = defaultdict(int)


async def run_test_scenario(runner, session_service, user_id, app_name, query, test_name):
    """Run a single test scenario."""
    current_session_id = str(uuid.uuid4())

    await session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        session_id=current_session_id,
    )

    print("\n" + "=" * 70)
    print(f"🧪 TEST: {test_name}")
    print("=" * 70)
    print(f"📝 Query: {query}")
    print("-" * 70)

    user_content = Content(parts=[Part.from_text(text=query)])

    async for event in runner.run_async(user_id=user_id, session_id=current_session_id, new_message=user_content):
        if not event.content or not event.content.parts:
            continue

        if event.is_streaming_tool_call():
            # Track streaming events
            for part in event.content.parts:
                if part.function_call:
                    tool_name = part.function_call.name
                    streaming_event_count[tool_name] += 1
                    args = part.function_call.args or {}
                    delta = args.get(TOOL_STREAMING_ARGS, "")
                    if delta:
                        preview = delta[:50] + "..." if len(delta) > 50 else delta
                        print(f"  ⏳ [Streaming] {tool_name}: {preview}")
            continue

        # Handle partial text responses
        if event.partial:
            for part in event.content.parts:
                if part.text:
                    print(part.text, end="", flush=True)
            continue

        # Handle complete events
        for part in event.content.parts:
            if part.thought:
                continue
            if part.function_call:
                tool_name = part.function_call.name
                tool_execution_count[tool_name] += 1
                print(f"\n  ✅ [Tool Complete] {tool_name}")
            elif part.function_response:
                response = part.function_response.response
                tool_type = response.get("tool_type", "unknown") if isinstance(response, dict) else "unknown"
                print(f"  📊 [Result] tool_type={tool_type}")
            elif part.text:
                print(f"\n  💬 {part.text[:100]}...")

    print("\n" + "-" * 70)


async def run_comprehensive_test():
    """Run comprehensive streaming tool tests."""

    app_name = "streaming_tool_comprehensive_test"

    from agent.agent import root_agent
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    user_id = "test_user"

    # Define test scenarios
    test_scenarios = [
        # Test 1: Sync function -> StreamingFunctionTool
        ("Use write_file tool to create a file named test.txt, content is a short poem about spring.",
         "Test 1: Sync function -> StreamingFunctionTool"),
        # Test 2: Async function -> StreamingFunctionTool
        ("Use async_write_file tool to create a Python script named async_test.py, implement a simple hello world program.",
         "Test 2: Async function -> StreamingFunctionTool"),
        # Test 3: FunctionTool -> StreamingFunctionTool
        ("Use append_file tool to append a log content to log.txt, record the current test status.",
         "Test 3: FunctionTool -> StreamingFunctionTool"),
        # Test 4: Custom BaseTool with is_streaming=True
        ("Use custom_write tool to create a configuration file named custom.json, include some application configurations.",
         "Test 4: Custom BaseTool with is_streaming=True"),
        # Test 5: ToolSet streaming tool
        ("Use _create_file tool to create a Markdown document named toolset_test.md, introduce ToolSet functionality.",
         "Test 5: ToolSet containing streaming tool"),
        # Test 6: StreamingFunctionTool wrapping a plain function
        ("Use save_document tool to save a document with the title \"Test Report\", content is about the test results of today.",
         "Test 6: StreamingFunctionTool wrapping a plain function"),
        # Test 7: Non-streaming tool comparison
        ("Use get_file_info tool to get the information of test.txt file.", "Test 7: Non-streaming tool (comparison)"),
        # Test 8: Mixed tools in one request
        ("Use write_file tool to create a index.html file, then use get_file_info tool to get its information.",
         "Test 8: Mixed streaming and non-streaming tools"),
    ]

    # Run all test scenarios
    for query, test_name in test_scenarios:
        await run_test_scenario(runner, session_service, user_id, app_name, query, test_name)

    # Print summary
    print("\n")
    print("╔" + "═" * 68 + "╗")
    print("║" + " TEST SUMMARY ".center(68) + "║")
    print("╠" + "═" * 68 + "╣")

    print("║" + " Streaming Events by Tool:".ljust(68) + "║")
    for tool, count in sorted(streaming_event_count.items()):
        line = f"   - {tool}: {count} streaming events"
        print("║" + line.ljust(68) + "║")

    print("║" + "".ljust(68) + "║")
    print("║" + " Tool Executions:".ljust(68) + "║")
    for tool, count in sorted(tool_execution_count.items()):
        line = f"   - {tool}: {count} executions"
        print("║" + line.ljust(68) + "║")

    print("╠" + "═" * 68 + "╣")

    # Verify results
    expected_streaming_tools = [
        "write_file", "async_write_file", "append_file", "custom_write", "_create_file", "save_document"
    ]
    expected_non_streaming_tools = ["get_file_info", "_read_file"]

    print("║" + " Verification:".ljust(68) + "║")

    all_passed = True
    for tool in expected_streaming_tools:
        if tool in streaming_event_count and streaming_event_count[tool] > 0:
            result = f"   ✅ {tool}: Streaming events detected"
        elif tool in tool_execution_count:
            result = f"   ⚠️  {tool}: Executed but no streaming events (may not have been called)"
            all_passed = False
        else:
            result = f"   ⏭️  {tool}: Not called in tests"
        print("║" + result.ljust(68) + "║")

    for tool in expected_non_streaming_tools:
        if tool in streaming_event_count and streaming_event_count[tool] > 0:
            result = f"   ❌ {tool}: Should NOT have streaming events!"
            all_passed = False
        elif tool in tool_execution_count:
            result = f"   ✅ {tool}: No streaming events (correct)"
        else:
            result = f"   ⏭️  {tool}: Not called in tests"
        print("║" + result.ljust(68) + "║")

    print("╠" + "═" * 68 + "╣")
    if all_passed:
        print("║" + " 🎉 ALL TESTS PASSED! ".center(68) + "║")
    else:
        print("║" + " ⚠️  SOME TESTS MAY NEED ATTENTION ".center(68) + "║")
    print("╚" + "═" * 68 + "╝")


if __name__ == "__main__":
    print("""
╔══════════════════════════════════════════════════════════════════════╗
║         Comprehensive Streaming Tool Test Suite                      ║
╠══════════════════════════════════════════════════════════════════════╣
║  Testing all streaming tool scenarios:                               ║
║  1. Sync function -> StreamingFunctionTool                           ║
║  2. Async function -> StreamingFunctionTool                          ║
║  3. FunctionTool -> StreamingFunctionTool                            ║
║  4. Custom BaseTool with is_streaming=True                           ║
║  5. ToolSet containing streaming tools                               ║
║  6. StreamingFunctionTool wrapping a plain function                   ║
║  7. Mixed configuration: ToolSet + FunctionTool + StreamingTool      ║
╚══════════════════════════════════════════════════════════════════════╝
    """)
    asyncio.run(run_comprehensive_test())
