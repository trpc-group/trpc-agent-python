# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Run an LLM Agent that stores its report with FileArtifactService."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from dotenv import load_dotenv

from trpc_agent_sdk.abc import ArtifactId
from trpc_agent_sdk.artifacts import FileArtifactService
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

APP_NAME = "artifact_report_demo"
USER_ID = "demo_user"

load_dotenv(Path(__file__).with_name(".env"))


async def run_artifact_agent() -> None:
    """Generate a report through a tool and verify filesystem persistence."""
    from agent.agent import root_agent

    session_id = str(uuid.uuid4())
    storage_dir = Path(__file__).parent / "artifact_data"
    artifact_service = FileArtifactService(storage_dir)
    session_service = InMemorySessionService()
    runner = Runner(
        agent=root_agent,
        app_name=APP_NAME,
        artifact_service=artifact_service,
        session_service=session_service,
    )

    query = ("Create a short Markdown quarterly report with sections for highlights, "
             "risks, and next steps, then save it as quarterly_report.md.")
    print(f"Session: {session_id}")
    print(f"User: {query}")
    print("Assistant: ", end="", flush=True)

    try:
        async for event in runner.run_async(
                user_id=USER_ID,
                session_id=session_id,
                new_message=Content(parts=[Part.from_text(text=query)]),
        ):
            if not event.content or not event.content.parts:
                continue
            for part in event.content.parts:
                if part.function_call:
                    print(f"\n[Invoke Tool: {part.function_call.name}"
                          f"({part.function_call.args})]")
                elif part.function_response:
                    print(f"\n[Tool Result: {part.function_response.response}]")
                elif part.text and not part.thought:
                    print(part.text, end="", flush=True)
    finally:
        await runner.close()

    # Recreate the service to prove that the artifact was persisted to disk,
    # rather than retained only by the original Python object.
    restarted_service = FileArtifactService(storage_dir)
    scope = ArtifactId(
        app_name=APP_NAME,
        session_id=session_id,
        user_id=USER_ID,
    )
    filenames = await restarted_service.list_artifact_keys(artifact_id=scope)
    print(f"\nPersisted artifacts after service restart: {filenames}")
    if "quarterly_report.md" not in filenames:
        raise RuntimeError("The agent did not persist quarterly_report.md")

    persisted = await restarted_service.load_artifact(artifact_id=scope.model_copy(
        update={"filename": "quarterly_report.md"}, ), )
    if persisted is None:
        raise RuntimeError("The persisted report could not be loaded")
    print(f"Persisted version: {persisted.version.version}")
    print(f"Storage directory: {storage_dir}")
    print("Persisted report content:")
    print(persisted.data.text)


if __name__ == "__main__":
    asyncio.run(run_artifact_agent())
