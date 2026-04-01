# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
#

import asyncio
import json
import os
import uuid

from claude_agent_sdk.types import ClaudeAgentOptions
from trpc_agent_sdk.models import OpenAIModel
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.server.agents.claude import ClaudeAgent
from trpc_agent_sdk.server.agents.claude import destroy_claude_env
from trpc_agent_sdk.server.agents.claude import setup_claude_env
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


def create_agent():
    """Create a ClaudeAgent with weather tool"""

    return ClaudeAgent(
        name="code_writing_agent",
        description="A helpful Claude assistant for writing code",
        model=OpenAIModel(
            model_name="deepseek-v3-local-II",
            api_key=os.environ.get("API_KEY", ""),
            base_url="http://v2.open.venus.woa.com/llmproxy",
        ),
        instruction="You are a helpful assistant for writing code.",
        claude_agent_options=ClaudeAgentOptions(allowed_tools=["Read", "Write", "Edit", "TodoWrite", "Glob", "Grep"], ),
    )


async def run_agent(runner: Runner, user_id: str, session_id: str, user_input: str):
    """Run Agent"""

    print(f"🆔 Session ID: {session_id[:8]}...")
    print(f"📝 用户: {user_input}")

    user_content = Content(parts=[Part.from_text(text=user_input)])

    print("\n🤖 Agent: ", end="", flush=True)
    async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=user_content):
        # Check if event.content exists
        if not event.content or not event.content.parts:
            continue

        if event.partial:
            for part in event.content.parts:
                if part.text:
                    print(part.text, end="", flush=True)
            continue

        for part in event.content.parts:
            if part.function_call:
                args_str = json.dumps(part.function_call.args, ensure_ascii=False)[:200]
                print(f"\n🔧 [Tool Call: {part.function_call.name}({args_str})]", flush=True)
            elif part.function_response:
                response_str = json.dumps(part.function_response.response, ensure_ascii=False)[:200]
                print(f"📊 [Tool Result: {part.function_response.name}({response_str})]", flush=True)
            # uncomment part.text to get the full text
            # elif part.text:
            #     print(f"\n[🤖 Agent:] {part.text}", flush=True)
    print("", flush=True)


async def main(agent: ClaudeAgent):
    """Main function to run Claude Agent"""

    session_service = InMemorySessionService()
    runner = Runner(app_name="claude_code_writing_app", agent=agent, session_service=session_service)

    user_id = "Alice"
    session_id = str(uuid.uuid4())

    reference = "/data/work/ai/trpc-agent-dev/trpc-agent/examples"

    try:
        await run_agent(
            runner,
            user_id,
            session_id,
            f"""
I'm using claude-agent-sdk to develop an agent.
Please review related code at '{reference}'.
And write to 'weather_agent.py' which use ClaudeAgent to current working directory.
""",
        )
    finally:
        await runner.close()


if __name__ == "__main__":
    # 设置Claude-Code默认调用的模型
    claude_default_model = OpenAIModel(
        model_name="deepseek-v3-local-II",
        api_key=os.environ.get("API_KEY", ""),
        base_url="http://v2.open.venus.woa.com/llmproxy",
    )
    # 设置proxy的host:port，初始化Claude Proxy Server进程
    setup_claude_env(proxy_host="0.0.0.0", proxy_port=8082,
                     claude_models={"all": claude_default_model})  # Maps to sonnet, opus, haiku

    # 创建ClaudeAgent并初始化
    agent = create_agent()
    agent.initialize()

    try:
        asyncio.run(main(agent))
    finally:
        # 进程退出需要销毁资源
        agent.destroy()
        destroy_claude_env()
        print("🧹 Claude environment cleaned up")
