# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Run the CodeAct generation-method example."""

import asyncio
import shutil
import uuid
from pathlib import Path

from dotenv import load_dotenv

from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import CodeActTool
from trpc_agent_sdk.types import Content, Part

load_dotenv(Path(__file__).with_name(".env"))


async def run_once(runner: Runner, *, title: str, query: str, expected: str) -> None:
    """Run one parent-Agent invocation and display its tool lifecycle."""
    print(f"\n📌 {title}")
    print(f"📝 User: {query}")
    print(f"🎯 Expected: {expected}")

    async for event in runner.run_async(
        user_id="demo_user",
        session_id=str(uuid.uuid4()),
        new_message=Content(parts=[Part.from_text(text=query)]),
    ):
        if not event.content:
            continue
        for part in event.content.parts:
            if part.thought:
                continue
            if part.function_call:
                print(
                    f"\n🔧 [Invoke Tool: "
                    f"{part.function_call.name}({part.function_call.args})]"
                )
            elif part.function_response:
                print(f"\n📦 [Tool Response: {part.function_response.response}]")
            elif part.text and event.is_final_response():
                print(f"\n✅ [Final Answer]\n{part.text}")


async def main() -> None:
    """Generate under prefer_approved, then enforce approved_only."""
    from agent.agent import create_agent

    current_dir = Path(__file__).parent
    code_act_dir = current_dir / ".codeact"
    shutil.rmtree(code_act_dir, ignore_errors=True)
    query = (
        "请务必调用 analyze_order_operations 分析以下订单，不要绕过工具"
        "直接回答。参数 overdue_days=3、high_value_threshold=1500。"
        "订单列表："
        "["
        '{"order_id":"O-1001","customer_id":"C-A","amount":1200,'
        '"status":"paid","days_since_update":5},'
        '{"order_id":"O-1002","customer_id":"C-B","amount":300,'
        '"status":"shipped","days_since_update":1},'
        '{"order_id":"O-1003","customer_id":"C-A","amount":800,'
        '"status":"delivered","days_since_update":0},'
        '{"order_id":"O-1004","customer_id":"C-C","amount":1500,'
        '"status":"cancelled","days_since_update":2},'
        '{"order_id":"O-1005","customer_id":"C-D","amount":700,'
        '"status":"shipped","days_since_update":6}'
        "]。"
    )

    print("🚀 TRPC Agent CodeAct Generation Method Example")

    prefer_agent = create_agent(code_act_dir, "prefer_approved")
    prefer_runner = Runner(
        app_name="codeact_prefer_approved_demo",
        agent=prefer_agent,
        session_service=InMemorySessionService(),
    )
    prefer_tool = next(
        tool for tool in prefer_agent.tools if isinstance(tool, CodeActTool)
    )
    print("\n=== Phase 1: prefer_approved ===")
    print(f"📦 Policy: {prefer_tool.implementation_policy.value}")
    print(f"💾 Store: {type(prefer_tool.implementation_store).__name__}")
    try:
        await run_once(
            prefer_runner,
            title="Generate a candidate because no approval exists",
            query=query,
            expected=(
                "active_amount = 3000; overdue = [O-1001, O-1005]; "
                "high_value_customers = [C-A]"
            ),
        )

        candidates = await prefer_tool.list_implementations()
        if not candidates:
            raise RuntimeError("No implementation candidate was saved")
        candidate = candidates[-1]
        print(f"\n🧾 [Candidate: {candidate.version}]")
        print("\n\n".join(candidate.code_cells))

        approved = await prefer_tool.approve(
            candidate.version,
            approved_by="generation-method-example",
        )
        print(f"\n✅ [Approved: {approved.version}]")
        candidate_versions = {
            implementation.version for implementation in candidates
        }
    finally:
        await prefer_runner.close()
        if prefer_tool.implementation_store is not None:
            await prefer_tool.implementation_store.close()

    approved_agent = create_agent(code_act_dir, "approved_only")
    approved_runner = Runner(
        app_name="codeact_approved_only_demo",
        agent=approved_agent,
        session_service=InMemorySessionService(),
    )
    approved_tool = next(
        tool for tool in approved_agent.tools if isinstance(tool, CodeActTool)
    )
    print("\n=== Phase 2: approved_only ===")
    print(f"📦 Policy: {approved_tool.implementation_policy.value}")
    print(f"💾 Store: {type(approved_tool.implementation_store).__name__}")
    try:
        await run_once(
            approved_runner,
            title="Execute only the approved implementation",
            query=query.replace(
                "high_value_threshold=1500",
                "high_value_threshold=2100",
            ),
            expected=(
                "active_amount = 3000; overdue = [O-1001, O-1005]; "
                "high_value_customers = []"
            ),
        )
        stored_versions = {
            implementation.version
            for implementation in await approved_tool.list_implementations()
        }
        if stored_versions != candidate_versions:
            raise RuntimeError(
                "approved_only unexpectedly changed implementation candidates"
            )
        print(
            f"\n🔒 [approved_only reused: {approved.version}; "
            "executed directly by the CodeAct runtime; no nested Agent or "
            "generation model was called]"
        )
    finally:
        await approved_runner.close()
        if approved_tool.implementation_store is not None:
            await approved_tool.implementation_store.close()
        shutil.rmtree(code_act_dir, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
