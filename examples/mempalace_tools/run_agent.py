# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

import asyncio
import os
import uuid

from dotenv import load_dotenv
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

# Load environment variables from the .env file
load_dotenv()


def _build_collection_where(wing: str, room: str) -> dict:
    """Build a Chroma where clause for this demo's MemPalace scope."""
    return {"$and": [{"wing": wing}, {"room": room}]}


def _truncate_tool_response(response: object, max_length: int = 512) -> str:
    """Keep demo output readable when a tool returns many memories."""
    text = str(response)
    if len(text) <= max_length:
        return text
    return f"{text[:max_length]}... <truncated, {len(text)} chars total>"


async def cleanup_mempalace_demo_data() -> None:
    """Delete data written by this demo so the next run starts clean."""

    def _cleanup() -> tuple[int, list[str]]:
        from agent.config import get_mempalace_config
        from mempalace.config import MempalaceConfig  # type: ignore[import-not-found]
        from mempalace.config import sanitize_name  # type: ignore[import-not-found]
        from mempalace.palace import get_collection  # type: ignore[import-not-found]

        mempalace_config = get_mempalace_config()
        config = MempalaceConfig()
        palace_path = mempalace_config["palace_path"] or config.palace_path
        wing = sanitize_name(mempalace_config["wing"], "wing")
        room = sanitize_name(mempalace_config["room"], "room")

        deleted_count = 0
        messages: list[str] = []
        col = get_collection(palace_path, collection_name=config.collection_name, create=False)

        for where in (
            _build_collection_where(wing, room),
            _build_collection_where(wing, "diary"),
            _build_collection_where("wing_personal_assistant", "diary"),
        ):
            results = col.get(where=where, include=[])
            ids = results.get("ids", []) if results else []
            if ids:
                col.delete(ids=ids)
                deleted_count += len(ids)

        kg_path = mempalace_config["kg_path"]
        if not kg_path and mempalace_config["palace_path"]:
            kg_path = os.path.join(mempalace_config["palace_path"], "knowledge_graph.sqlite3")

        if kg_path:
            for suffix in ("", "-wal", "-shm"):
                path = f"{kg_path}{suffix}"
                if os.path.exists(path):
                    os.remove(path)
                    messages.append(f"deleted {path}")
        else:
            messages.append("skip KG cleanup because MEMPALACE_KG_PATH or MEMPALACE_PALACE_PATH is not set")

        return deleted_count, messages

    try:
        deleted_count, messages = await asyncio.to_thread(_cleanup)
        print(f"🧹 Cleaned MemPalace demo drawers: {deleted_count}")
        for message in messages:
            print(f"🧹 {message}")
    except Exception as exc:  # pylint: disable=broad-except
        print(f"⚠️ Failed to clean MemPalace demo data: {exc}")


async def run_mempalace_agent(*, title: str, demo_queries: list[str]):
    """Run one phase of the MemPalace tools agent demo."""

    app_name = "mempalace_memory_assistant"

    from agent.agent import root_agent
    session_service = InMemorySessionService()
    runner = Runner(app_name=app_name, agent=root_agent, session_service=session_service)

    user_id = "alice"

    print("=" * 60)
    print(title)
    print("=" * 60)

    for query in demo_queries:
        # Use a new session for each query
        current_session_id = str(uuid.uuid4())

        print(f"🆔 Session ID: {current_session_id[:8]}...")
        print(f"📝 User: {query}")

        user_content = Content(parts=[Part.from_text(text=query)])

        print("🤖 Assistant: ", end="", flush=True)
        async for event in runner.run_async(user_id=user_id, session_id=current_session_id, new_message=user_content):
            # Check if event.content exists
            if not event.content or not event.content.parts:
                continue

            if event.partial:
                for part in event.content.parts:
                    if part.text:
                        print(part.text, end="", flush=True)
                continue

            for part in event.content.parts:
                # Skip the reasoning part; the output is already generated when partial=True
                if part.thought:
                    continue
                if part.function_call:
                    print(f"\n🔧 [Invoke Tool: {part.function_call.name}({part.function_call.args})]")
                elif part.function_response:
                    print(f"📊 [Tool Result: {_truncate_tool_response(part.function_response.response)}]")
                # Uncomment to get the full text output of the LLM
                # elif part.text:
                #     print(f"\n✅ {part.text}")

        print("\n" + "-" * 40)


async def main():
    initial_queries = [
        "Use mempalace_search to check whether you remember my name.",
        "Use mempalace_add_drawer to remember that my name is Alice.",
        "Use mempalace_add_drawer to remember that my favorite food is Italian food.",
        "Use mempalace_search to recall my name and favorite food.",
        "Use mempalace_diary_write to write a diary entry: Alice tested the MemPalace tools example today.",
        "Use mempalace_diary_read to read the latest diary entries.",
        "Use mempalace_kg_add to add this fact: Alice likes Italian food.",
        "Use mempalace_kg_query to query facts about Alice.",
        "Use mempalace_kg_timeline to show Alice's knowledge graph timeline.",
    ]
    persistence_queries = [
        "Use mempalace_search to recall my name and favorite food from the previous sessions.",
        "Use mempalace_diary_read to read the latest diary entries from the previous sessions.",
        "Use mempalace_kg_query to query facts about Alice from the previous sessions.",
        "Use mempalace_kg_timeline to show Alice's knowledge graph timeline from the previous sessions.",
    ]
    invalidation_queries = [
        "Use mempalace_kg_invalidate to mark the fact Alice likes Italian food as ended today.",
        "Use mempalace_kg_query to query facts about Alice again after invalidation.",
    ]

    await cleanup_mempalace_demo_data()
    try:
        await run_mempalace_agent(
            title="First phase: write memories and verify cross-session reads",
            demo_queries=initial_queries,
        )
        print("Sleeping for 2 seconds before persistence verification...")
        await asyncio.sleep(2)
        await run_mempalace_agent(
            title="Second phase: read previously stored data with new sessions",
            demo_queries=persistence_queries,
        )
        await run_mempalace_agent(
            title="Third phase: test KG invalidation after persistence verification",
            demo_queries=invalidation_queries,
        )
    finally:
        await cleanup_mempalace_demo_data()


if __name__ == "__main__":
    asyncio.run(main())
