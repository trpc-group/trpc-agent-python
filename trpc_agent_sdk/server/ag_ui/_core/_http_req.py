# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Utilities for AG-UI HTTP request access in InvocationContext."""

from starlette.requests import Request

from trpc_agent_sdk.configs import RunConfig
from trpc_agent_sdk.context import InvocationContext

_AGUI_HTTP_REQ_KEY: str = "_trpc_ag_ui_http_req"


def set_agui_http_req(run_config: RunConfig, request: Request) -> None:
    """Inject AG-UI HTTP request into run_config for current invocation."""
    run_config.agent_run_config[_AGUI_HTTP_REQ_KEY] = request


def get_agui_http_req(ctx: InvocationContext) -> Request | None:
    """Get AG-UI HTTP request from invocation context run_config."""
    if ctx.run_config is None:
        return None

    request = ctx.run_config.agent_run_config.get(_AGUI_HTTP_REQ_KEY)
    if not isinstance(request, Request):
        return None
    return request
