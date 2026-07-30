# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
#
# Below code are copy and modified from https://github.com/ag-ui-protocol/ag-ui.git
#
# MIT License
#
# Copyright (c) 2025
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
"""FastAPI endpoint for TRPC Agent middleware."""

from ag_ui.core import RunAgentInput
from ag_ui.encoder import EventEncoder
from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import StreamingResponse
from trpc_agent_sdk.log import logger

from ._agui_agent import AgUiAgent


def add_trpc_fastapi_endpoint(app: FastAPI, agent: AgUiAgent, path: str = "/"):
    """Add TRPC Agent middleware endpoint to FastAPI app.

    Args:
        app: FastAPI application instance
        agent: Configured AgUiAgent instance
        path: API endpoint path
    """

    @app.post(path)
    async def trpc_endpoint(input_data: RunAgentInput, request: Request):  # pylint: disable=unused-variable
        """TRPC Agent middleware endpoint."""

        # Get the accept header from the request
        accept_header = request.headers.get("accept")

        # Create an event encoder to properly format SSE events
        encoder = EventEncoder(accept=accept_header)

        async def event_generator():
            """Generate events from TRPC agent."""
            try:
                async for event in agent.run(input_data, http_request=request):
                    try:
                        encoded = encoder.encode(event)
                        logger.debug("HTTP Response: %s", encoded)
                        yield encoded
                    except Exception as encoding_error:  # pylint: disable=broad-except
                        # Handle encoding-specific errors
                        logger.error("❌ Event encoding error: %s", encoding_error,
                                     exc_info=True)  # Create a RunErrorEvent for encoding failures
                        from ag_ui.core import RunErrorEvent, EventType

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
                            yield 'event: error\ndata: {"error": "Event encoding failed"}\n\n'
                        break  # Stop the stream after an encoding error
            except Exception as agent_error:  # pylint: disable=broad-except
                # Handle errors from AgUiAgent.run() itself
                logger.error(
                    "❌ AgUiAgent error: %s", agent_error,
                    exc_info=True)  # AgUiAgent should have yielded a RunErrorEvent, but if something went wrong
                # in the async generator itself, we need to handle it
                try:
                    from ag_ui.core import RunErrorEvent, EventType

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
                    yield 'event: error\ndata: {"error": "Agent execution failed"}\n\n'

        return StreamingResponse(event_generator(), media_type=encoder.get_content_type())


def create_trpc_app(agent: AgUiAgent, path: str = "/") -> FastAPI:
    """Create a FastAPI app with TRPC Agent middleware endpoint.

    Args:
        agent: Configured AgUiAgent instance
        path: API endpoint path

    Returns:
        FastAPI application instance
    """
    app = FastAPI(title="TRPC Agent Middleware for AG-UI Protocol")
    add_trpc_fastapi_endpoint(app, agent, path)
    return app
