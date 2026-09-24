# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

import asyncio
import uuid
from pathlib import Path

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

# Load environment variables from the .env file
load_dotenv()


async def run_vision_agent():
    """Run the vision-language agent demo to demonstrate multimodal conversation."""

    app_name = "vision_agent_demo"

    from agent.agent import root_agent
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    user_id = "demo_user"

    # Sample image: a math problem with a trigonometric function figure
    image_path = Path(__file__).parent / "images" / "sample_problem.jpg"
    extract_info_question = (
        "Read the image carefully. Summarize the problem text and extract the "
        "key information from the figure, such as the amplitude, period, phase "
        "and any marked points. Do not solve the problem or perform any "
        "calculation; solving is left to the next turn."
    )
    solve_question = (
        "Based on the key information you summarized and the problem text "
        "in the image, solve the problem and provide the complete solution."
    )

    # Read the sample image once, reused in both turns
    image_part = Part.from_bytes(data=image_path.read_bytes(), mime_type="image/jpeg")

    # Both turns share the same session_id so the model can reference
    # the extracted information from turn 1 when solving in turn 2
    current_session_id = str(uuid.uuid4())
    await session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        session_id=current_session_id,
        state={
            "user_name": f"{user_id}",
        },
    )

    demo_turns = [
        ("Turn 1: Extract key information from the image",
         Content(parts=[Part.from_text(text=extract_info_question), image_part])),
        ("Turn 2: Solve the problem",
         Content(parts=[Part.from_text(text=solve_question), image_part])),
    ]

    for title, user_content in demo_turns:
        print(f"🆔 Session ID: {current_session_id[:8]}...")
        print(f"📝 {title}")

        print("🤖 Assistant: ", end="", flush=True)
        async for event in runner.run_async(user_id=user_id, session_id=current_session_id, new_message=user_content):
            if not event.content or not event.content.parts:
                continue

            if event.partial:
                for part in event.content.parts:
                    if part.thought:
                        continue
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
    asyncio.run(run_vision_agent())
