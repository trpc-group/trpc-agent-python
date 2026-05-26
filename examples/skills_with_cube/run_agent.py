#!/usr/bin/env python3

# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
Example demonstrating the skills run flow in TRPC Agent framework.
"""
import asyncio
import json
import os
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()


async def _kill_remote_sandbox_for_auto_recover_test(workspace_runtime) -> None:
    """Delete the remote Cube sandbox without clearing the local client.

    This is intentionally test-only code: keeping the stale local handle lets
    the next workspace operation hit SandboxNotFoundException and exercise the
    CubeSandboxClient auto-recovery path.
    """
    client = workspace_runtime._client  # pylint: disable=protected-access
    sandbox = client._require()  # pylint: disable=protected-access
    old_sandbox_id = sandbox.sandbox_id
    await sandbox.kill()
    print(
        f"[skills_with_cube] killed remote Cube sandbox for auto-recover test: {old_sandbox_id}",
        flush=True,
    )


async def run_skill_run_demo():
    """Run the skill run agent demo to demonstrate the various capabilities of an LLM agent."""

    app_name = "skill_run_agent_demo"

    from agent.agent import create_agent
    from agent.tools import build_cube_skill_run_payload

    root_agent, runtime_handle = await create_agent()
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    user_id = "demo_user"

    cube_payload = build_cube_skill_run_payload(
        skill_name="python-math",
        inputs_host="/tmp/skillrun-inputs",
    )
    cube_stage_inputs_request = f"""
        Cube stage_inputs demonstration.
        Please call skill_run once using this payload shape exactly:
        {json.dumps(cube_payload, ensure_ascii=False)}

        Notes:
        1) The current runtime is Cube, so host:// inputs are uploaded into the remote sandbox.
        2) If artifact service is unavailable, continue with host://, workspace://, skill://.
        3) After running, explain which input schemes were staged successfully and include output file summaries.
    """

    demo_queries = [cube_stage_inputs_request]
    recreate_between_runs = os.getenv("SKILLS_WITH_CUBE_RECREATE_BETWEEN_RUNS", "").lower() in {"1", "true", "yes"}
    kill_between_runs = os.getenv("SKILLS_WITH_CUBE_KILL_BETWEEN_RUNS", "").lower() in {"1", "true", "yes"}
    if recreate_between_runs or kill_between_runs:
        demo_queries.append(cube_stage_inputs_request)

    try:
        for idx, query in enumerate(demo_queries):
            if idx == 1:
                if kill_between_runs:
                    print("[skills_with_cube] killing Cube sandbox before the next request...", flush=True)
                    await _kill_remote_sandbox_for_auto_recover_test(runtime_handle)
                else:
                    print("[skills_with_cube] recreating Cube sandbox before the next request...", flush=True)
                    await runtime_handle.recreate()
                    print(f"[skills_with_cube] using Cube sandbox: {runtime_handle.sandbox_id}", flush=True)

            current_session_id = str(uuid.uuid4())

            print(f"🆔 Session ID: {current_session_id[:8]}...")
            print(f"📝 User: {query}")

            user_content = Content(parts=[Part.from_text(text=query)])

            print("🤖 Assistant: ", end="", flush=True)
            async for event in runner.run_async(user_id=user_id, session_id=current_session_id, new_message=user_content):
                if not event.content or not event.content.parts:
                    continue

                for part in event.content.parts:
                    if event.partial:
                        if part.text:
                            print(part.text, end="", flush=True)
                        continue

                    if part.thought:
                        continue
                    if part.function_call:
                        print(f"\n🔧 [Invoke Tool:: {part.function_call.name}({part.function_call.args})]")
                    elif part.function_response:
                        print(f"📊 [Tool Result: {part.function_response.response}]")
                    # elif part.text:
                    #     print(f"\n✅ {part.text}")

            print("\n" + "-" * 40)
    finally:
        await runner.close()
        await runtime_handle.destroy()


if __name__ == "__main__":
    os.system("echo 'hello from skillrun' > /tmp/skillrun-notes.txt")
    os.system("echo 'this is another line' >> /tmp/skillrun-notes.txt")
    os.system("mkdir -p /tmp/skillrun-inputs")
    os.system("""cat > /tmp/skillrun-inputs/sales.csv << 'EOF'
region,amount
north,100
south,200
EOF
""")
    # Create sample CSV file for data analysis skill
    os.system("""cat > /tmp/sales_data.csv << 'EOF'
Date,Product,Sales,Quantity,Region
2024-01-01,Product A,1000,10,North
2024-01-02,Product B,1500,15,South
2024-01-03,Product A,1200,12,North
2024-01-04,Product C,800,8,East
2024-01-05,Product B,2000,20,South
2024-01-06,Product A,900,9,West
2024-01-07,Product C,1100,11,East
2024-01-08,Product B,1800,18,North
EOF
""")
    asyncio.run(run_skill_run_demo())
    os.system("rm -rf /tmp/skillrun-inputs/*")
    os.system("rm -rf /tmp/sales_data.csv")
