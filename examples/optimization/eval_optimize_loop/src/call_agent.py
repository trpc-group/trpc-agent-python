"""PlateAgent call_agent adapter for AgentOptimizer.

Provides echo_call_agent (fast validation) and create_plate_call_agent
(real OCR pipeline) as async (query: str) -> str callables.
"""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


async def echo_call_agent(query: str) -> str:
    """Echo call_agent: extract [answer: xxx] from query.

    Used for fast validation without PlateAgent dependency.
    """
    match = re.search(r"\[answer:\s*(.+?)\]", query)
    if match:
        return match.group(1).strip()
    return f"echo: {query[:80]}"


def create_plate_call_agent(
    plate_agent_root: str,
    prompt_dir: Optional[str] = None,
) -> "callable":
    """Create a PlateAgent call_agent for AgentOptimizer.

    Returns async (query: str) -> str.
    Each call re-reads prompt from disk and creates a fresh session
    to ensure evaluation isolation.
    """
    import sys
    _plate_root = str(Path(plate_agent_root))
    sys.path.insert(0, _plate_root)
    try:
        async def _call_agent(query: str) -> str:
        try:
            from trpc_agent_sdk.runners import Runner
            from trpc_agent_sdk.sessions import InMemorySessionService
            from trpc_agent_sdk.types import Content, Part
        except ImportError:
            raise ImportError("plate_call_agent requires trpc_agent_sdk.")

        try:
            from agent.graph_agent import recognition_agent
        except ImportError as e:
            raise ImportError(
                f"Cannot import PlateAgent modules from {plate_agent_root}: {e}"
            )

        image_path = _resolve_image_path(query, plate_agent_root)

        session_service = InMemorySessionService()
        runner = Runner(
            app_name="plate_optimizer",
            agent=recognition_agent,
            session_service=session_service,
        )

        session_id = str(uuid.uuid4())
        await session_service.create_session(
            app_name="plate_optimizer",
            user_id="optimizer",
            session_id=session_id,
            state={"image_path": image_path} if image_path else {},
        )

        message = "Identify this license plate image."
        user_content = Content(parts=[Part.from_text(text=message)])

        final_text = ""
        try:
            async for event in runner.run_async(
                user_id="optimizer",
                session_id=session_id,
                new_message=user_content,
            ):
                if not event.is_final_response():
                    continue
                if not event.content or not event.content.parts:
                    continue
                for part in event.content.parts:
                    if part.thought:
                        continue
                    if part.text:
                        final_text += part.text
        except Exception as e:
            logger.exception("runner.run_async failed, attempting session.state fallback")
            try:
                session = await session_service.get_session(
                    app_name="plate_optimizer",
                    user_id="optimizer",
                    session_id=session_id,
                )
                if session and session.state:
                    final_text = session.state.get("final_plate", "")
                    if not final_text:
                        final_text = str(session.state.get("last_response", ""))
            except Exception:
                pass

        return final_text.strip() or "recognition failed"

    finally:
        try:
            sys.path.remove(_plate_root)
        except ValueError:
            pass
    return _call_agent


def _resolve_image_path(query: str, plate_agent_root: str) -> str:
    """Extract image path from query (supports image: prefix)."""
    match = re.search(r"image:\s*(\S+)", query)
    if match:
        return _ensure_abs(match.group(1), plate_agent_root)
    match = re.search(r"(\S+\.(?:jpg|jpeg|png|bmp))", query)
    if match:
        return _ensure_abs(match.group(1), plate_agent_root)
    return ""


def _ensure_abs(path: str, plate_agent_root: str) -> str:
    """Resolve relative image path against common plate-agent directories."""
    p = Path(path)
    if p.is_absolute():
        return str(p)
    candidates = [
        Path(plate_agent_root) / path,
        Path(plate_agent_root) / "eval" / "dataset" / "test_plates" / path,
        Path(plate_agent_root) / "test_images" / path,
    ]
    for cand in candidates:
        if cand.exists():
            return str(cand)
    return str(Path(plate_agent_root) / path)
