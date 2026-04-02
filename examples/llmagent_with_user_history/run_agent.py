# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

import asyncio
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.configs import RunConfig
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

# Load environment variables from the .env file
load_dotenv()


async def run_user_history_demo():
    """ run user history injection demo.

    Demonstrate how to inject user history into the Agent's context through HistoryRecord,
    so that the Agent can answer questions based on historical information without calling tools.
    """

    app_name = "user_history_demo"

    from agent.agent import root_agent
    from agent.tools import make_user_history_record

    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    user_id = "demo_user"
    # multiple turns of conversation share the same session_id, so that the Agent can see the historical messages of this session
    session_id = str(uuid.uuid4())

    demo_queries = [
        "What's your name?",
        "what is the weather like in paris?",
        "Do you remember my name?",
    ]

    for query in demo_queries:
        print(f"📝 User: {query}")

        # construct user history record, and build context content based on the current query
        # history_content will contain historical question-answer pairs related to the query, for Agent reference
        history_record = make_user_history_record()
        history_content = history_record.build_content(query)
        user_content = Content(parts=[Part.from_text(text=query)])

        print("🤖 Assistant: ", end="", flush=True)
        # enable session history saving, so that subsequent turns can accumulate context
        run_config = RunConfig(save_history_enabled=True)
        # new_message is passed in as a list [history_content, user_content],
        # so that the historical record and the user's current question are injected into the Agent's input
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=[history_content, user_content],
            run_config=run_config,
        ):
            # skip empty content event
            if not event.content or not event.content.parts:
                continue

            # partial=True means the intermediate fragment of streaming output, print text in real time
            if event.partial:
                for part in event.content.parts:
                    if part.text:
                        print(part.text, end="", flush=True)
                continue

            # complete event: print tool call and tool return result, skip thinking process
            for part in event.content.parts:
                if part.thought:
                    continue
                if part.function_call:
                    print(f"\n🔧 [Invoke Tool: {part.function_call.name}({part.function_call.args})")
                elif part.function_response:
                    print(f"📊 [Tool Result: {part.function_response.response}]")

        print("\n" + "-" * 40)


if __name__ == "__main__":
    asyncio.run(run_user_history_demo())
