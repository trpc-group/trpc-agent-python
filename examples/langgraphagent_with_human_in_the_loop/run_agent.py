import asyncio
import uuid
from dataclasses import dataclass
from typing import Optional

from trpc_agent.runners import Runner
from trpc_agent.sessions import InMemorySessionService
from trpc_agent.agents import LangGraphAgent
from trpc_agent.events import LongRunningEvent
from trpc_agent.types import Content, Part, FunctionResponse

from dotenv import load_dotenv
# Load environment variables from the .env file
load_dotenv()


@dataclass
class InvocationParams:
    """Parameters for running an invocation"""

    user_id: str
    session_id: str
    agent: LangGraphAgent
    session_service: InMemorySessionService
    app_name: str


async def run_invocation(
    params: InvocationParams,
    content: Content,
) -> Optional[LongRunningEvent]:
    """Run an invocation with a fresh runner instance.

    Args:
        params: Invocation parameters containing user_id, session_id, agent, and session_service
        content: The content to send to the agent

    Returns:
        LongRunningEvent if one is encountered, None otherwise
    """
    runner = Runner(app_name=params.app_name, agent=params.agent, session_service=params.session_service)

    captured_long_running_event = None

    try:
        async for event in runner.run_async(user_id=params.user_id, session_id=params.session_id, new_message=content):
            if isinstance(event, LongRunningEvent):
                captured_long_running_event = event
                print(f"\n🔄 [Long-running operation detected]")
                print(f"   Function: {event.function_call.name}")
                print(f"   Response: {event.function_response.response}")
                print("   ⏳ Waiting for human intervention...")
            elif event.content and event.content.parts and event.author != "user":
                if event.partial:
                    for part in event.content.parts:
                        if part.text:
                            print(part.text, end="", flush=True)
                else:
                    for part in event.content.parts:
                        if part.function_call:
                            print(f"\n🔧 [Calling tool: {part.function_call.name}]")
                            print(f"   Args: {part.function_call.args}")
                        elif part.function_response:
                            print(f"📊 [Tool result: {part.function_response.response}]")
    finally:
        await runner.close()

    return captured_long_running_event


async def run_human_in_loop_agent():
    """Run the agent with support for long-running events"""

    print("🔧 LangGraph Human-In-The-Loop Demo")
    print("=" * 60)
    print("This demo shows how to handle human approval in LangGraph using interrupts.")
    print("=" * 60)

    app_name = "langgraph_human_in_loop_demo"

    from agent.agent import root_agent
    session_service = InMemorySessionService()

    params = InvocationParams(
        user_id="demo_user",
        session_id=str(uuid.uuid4()),
        agent=root_agent,
        session_service=session_service,
        app_name=app_name,
    )

    query = "I need to delete the production database 'user_data' for migration purposes. The details are: environment=prod, backup_created=true, reason=migration_to_new_system"

    print(f"\n📝 User: {query}")
    print("🤖 Assistant: ", end="", flush=True)

    user_content = Content(parts=[Part.from_text(text=query)])

    long_running_event = await run_invocation(params, user_content)

    if long_running_event:
        print("\n👤 Human intervention simulation...")
        await asyncio.sleep(2)

        function_name = long_running_event.function_call.name
        response_data = long_running_event.function_response.response
        print(f"🤖 Assistant: {function_name}: {response_data}")

        human_decision = "approved"  # or "rejected"
        print(f"   Human decision: {human_decision}")

        resume_data = {"status": human_decision}

        resume_function_response = FunctionResponse(
            id=long_running_event.function_response.id,
            name=long_running_event.function_response.name,
            response=resume_data,
        )
        resume_content = Content(role="user", parts=[Part(function_response=resume_function_response)])

        print("\n🔄 Resuming agent execution...")

        await run_invocation(params, resume_content)

    print("\n✅ LangGraph Human-In-The-Loop Demo completed!")


if __name__ == "__main__":
    try:
        asyncio.run(run_human_in_loop_agent())
    except KeyboardInterrupt:
        print("\n\n👋 Demo interrupted")
    except Exception as e:
        print(f"\n❌ Error during demo: {e}")
        raise
