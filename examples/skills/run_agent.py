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
import os
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

load_dotenv()


async def run_skill_run_demo():
    """Run the skill run agent demo to demonstrate the various capabilities of an LLM agent."""

    app_name = "skill_run_agent_demo"

    from agent.agent import root_agent
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    user_id = "demo_user"

    user_file_ops_request = """
        I have a text file at /tmp/skillrun-notes.txt.
        Please use the user-file-ops skill to summarize it, you can use command `cp` to copy it to the workspace,
        then mapping it to `work/inputs/user-notes.txt` and writing the summary to `out/user-notes-summary.txt`
    """
    py_math_request = """
        Please use the python-math skill to calculate the first 10 Fibonacci numbers.
        mapping the result to `out/fib.txt`
    """
    file_tools_request = """
        Please use the file-tools skill to write a sample file to out/sample.txt with the content "Hello from skill"
        and then create a tar.gz archive of the out/ folder.
        mapping the result to out/sample.tgz
    """
    data_analysis_request = """
        I have a CSV file at /tmp/sales_data.csv with sales data.
        Please use the data-analysis skill to perform comprehensive analysis. You can use command `cp` to copy it to the workspace,
        then mapping it to `work/inputs/sales_data.csv`.

        Please execute all three analysis scripts from the data-analysis skill:
        1. Use scripts/analyze_csv.py to generate a basic analysis report and save it to out/analysis_report.txt
        2. Use scripts/describe_data.py to calculate descriptive statistics and save it to out/sales_stats.txt
        3. Use scripts/summarize_data.py to generate a comprehensive summary report and save it to out/dataset_summary.txt

        Make sure to reference the documentation in the references subdirectory for pandas and numpy usage.
        Ensure all three scripts are executed to provide complete data analysis.
    """

    demo_queries = [
        user_file_ops_request,
        py_math_request,
        file_tools_request,
        data_analysis_request,
    ]

    for query in demo_queries:
        current_session_id = str(uuid.uuid4())

        print(f"🆔 Session ID: {current_session_id[:8]}...")
        print(f"📝 User: {query}")

        user_content = Content(parts=[Part.from_text(text=query)])

        print("🤖 Assistant: ", end="", flush=True)
        async for event in runner.run_async(user_id=user_id, session_id=current_session_id, new_message=user_content):
            if not event.content or not event.content.parts:
                continue

            if event.partial:
                for part in event.content.parts:
                    if part.text:
                        print(part.text, end="", flush=True)
                continue

            for part in event.content.parts:
                if part.thought:
                    continue
                if part.function_call:
                    print(f"\n🔧 [Invoke Tool:: {part.function_call.name}({part.function_call.args})]")
                elif part.function_response:
                    print(f"📊 [Tool Result: {part.function_response.response}]")
                # elif part.text:
                #     print(f"\n✅ {part.text}")

        print("\n" + "-" * 40)


if __name__ == "__main__":
    os.system("rm -rf /tmp/ws_*")
    os.system("echo 'hello from skillrun' > /tmp/skillrun-notes.txt")
    os.system("echo 'this is another line' >> /tmp/skillrun-notes.txt")
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
