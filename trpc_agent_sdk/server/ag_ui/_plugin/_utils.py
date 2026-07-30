# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""AG-UI agent endpoint with tRPC context and filters."""

import asyncio

from ag_ui.core import EventType
from ag_ui.core import RunAgentInput
from ag_ui.core import RunErrorEvent
from ag_ui.encoder import EventEncoder
from fastapi import Request
from trpc_agent_sdk.log import logger

from .._core import AgUiAgent


async def event_generator(request: Request, agui_agent: AgUiAgent, input_data: RunAgentInput, encoder: EventEncoder):
    """Generate events from AG-UI agent."""
    try:
        app_name = agui_agent.get_app_name(input_data)
        user_id = agui_agent.get_user_id(input_data)
        session_id = input_data.thread_id
        # Get tRPC context from contextvars (injected by middleware)
        async for event in agui_agent.run(input_data, http_request=request):
            # Check for client disconnect periodically
            if await request.is_disconnected():
                logger.info("Client disconnected for thread %s", session_id)
                break

            try:
                encoded = encoder.encode(event)
                logger.debug("HTTP Response: %s", encoded)
                yield encoded
            except Exception as encoding_error:  # pylint: disable=broad-except
                # Handle encoding-specific errors
                logger.error("❌ Event encoding error: %s", encoding_error, exc_info=True)
                # Create a RunErrorEvent for encoding failures
                error_event = RunErrorEvent(
                    type=EventType.RUN_ERROR,
                    message=f"Event encoding failed: {str(encoding_error)}",
                    code="ENCODING_ERROR",
                )
                try:
                    error_encoded = encoder.encode(error_event)
                    yield error_encoded
                except Exception:  # pylint: disable=broad-except
                    # If we can't even encode the error event, yield a basic SSE error
                    logger.error("Failed to encode error event, yielding basic SSE error")
                    yield 'event: error\\ndata: {\\"error\\": \\"Event encoding failed\\"}\\n\\n'
                break  # Stop the stream after an encoding error
    except asyncio.CancelledError:
        # Connection was closed by client
        logger.info("Connection cancelled for thread %s", session_id)
        raise
    except Exception as agent_error:  # pylint: disable=broad-except
        # Handle errors from AgUiAgent.run() itself
        logger.error("❌ AgUiAgent error: %s", agent_error, exc_info=True)
        try:
            error_event = RunErrorEvent(
                type=EventType.RUN_ERROR,
                message=f"Agent execution failed: {str(agent_error)}",
                code="AGENT_ERROR",
            )
            error_encoded = encoder.encode(error_event)
            yield error_encoded
        except Exception:  # pylint: disable=broad-except
            # If we can't encode the error event, yield a basic SSE error
            logger.error("Failed to encode agent error event, yielding basic SSE error")
            yield 'event: error\\ndata: {\\"error\\": \\"Agent execution failed\\"}\\n\\n'
    finally:
        # Trigger cancellation of the background TRPC run
        # Uses the configured cancel_wait_timeout from AgUiAgent
        await agui_agent.cancel_run(
            session_id=session_id,
            app_name=app_name,
            user_id=user_id,
        )
