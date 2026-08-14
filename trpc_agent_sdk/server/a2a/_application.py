# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
#
# Below code are copy and modified from https://github.com/google/adk-python.git
#
# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Server application assembly for trpc-agent as an A2A service.

This module wraps the a2a-sdk 1.x route factories
(``create_agent_card_routes`` / ``create_jsonrpc_routes``) so that business
code and examples never need to import ``a2a.server.*`` directly.  It also
exposes the ``enable_v0_3_compat`` switch for accepting legacy 0.3 clients.

``create_a2a_application`` is an *optional convenience layer*, not the only
path: every a2a-sdk component it uses is a public API, so callers who need full
control over the assembled ``Starlette`` app may bypass it and compose the route
factories themselves (see ``trpc_agent_sdk/server/a2a/README.md`` §3.1b).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes
from a2a.server.routes import create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore
from a2a.types import AgentInterface

from trpc_agent_sdk.log import logger

if TYPE_CHECKING:
    from starlette.applications import Starlette

    from ._agent_service import TrpcA2aAgentService


def create_a2a_application(
    a2a_svc: "TrpcA2aAgentService",
    *,
    enable_v0_3_compat: bool = False,
    request_handler: "DefaultRequestHandler | None" = None,
) -> "Starlette":
    """Assemble a Starlette app that serves a trpc-agent as an A2A agent.

    Args:
        a2a_svc: The initialized :class:`TrpcA2aAgentService` to serve.  Its
            agent card may leave ``supported_interfaces[].url`` empty (built
            without ``TrpcA2aAgentService(rpc_url=...)``); this is logged as a
            warning so discovery-based clients fail loudly, while JSON-RPC calls
            that do not use the card are unaffected.
        enable_v0_3_compat: Whether to accept legacy v0.3 clients on the same
            endpoint (see the a2a-sdk migration guide).
        request_handler: Optional fully-customized A2A request handler (advanced
            usage).  When provided it is used as-is, giving full control over the
            handler's configuration (task store, push notifications, extended
            cards, ...).  Defaults to a ``DefaultRequestHandler`` built from
            ``a2a_svc`` with an in-memory task store.

    Returns:
        A Starlette application wired with the agent-card and JSON-RPC routes.
    """
    from starlette.applications import Starlette

    request_handler = request_handler or DefaultRequestHandler(
        agent_executor=a2a_svc,
        task_store=InMemoryTaskStore(),
        agent_card=a2a_svc.agent_card,
    )
    if a2a_svc.agent_card is not None:
        _ensure_card_has_url(a2a_svc.agent_card)
        if enable_v0_3_compat:
            _ensure_v0_3_interface(a2a_svc.agent_card)
    routes: list[Any] = []
    routes.extend(create_agent_card_routes(a2a_svc.agent_card))
    routes.extend(
        create_jsonrpc_routes(
            request_handler,
            rpc_url=_jsonrpc_path_from_card(a2a_svc.agent_card),
            enable_v0_3_compat=enable_v0_3_compat,
        ))
    return Starlette(routes=routes)


def _jsonrpc_path_from_card(card: Any) -> str:
    """Derive the JSON-RPC mount path from the card's advertised url.

    The path where the JSON-RPC endpoint is mounted must match the url advertised
    in ``supported_interfaces[].url``, otherwise clients discover one path and
    call another.  Rather than letting the caller configure a second path that
    has to be kept in sync with the card, derive it from the card itself so the
    two can never diverge: take the path component of the first advertised
    JSONRPC/HTTP+JSON url (defaulting to ``/`` for a bare origin).  A card built
    by the framework has a single interface, so "first" is the one 1.x clients
    discover; multi-endpoint cards are outside this convenience layer's scope.

    Args:
        card: The agent card to derive the path from (a2a-sdk protobuf message).

    Returns:
        The mount path (e.g. ``/`` or ``/a2a``), starting with ``/``.
    """
    advertised = next(
        (i.url for i in card.supported_interfaces if i.protocol_binding in ("JSONRPC", "HTTP+JSON") and i.url),
        None,
    )
    if advertised is None:
        return "/"
    from urllib.parse import urlparse

    path = urlparse(advertised).path
    return path if path.startswith("/") else "/"


def _ensure_card_has_url(card: Any) -> None:
    """Warn if the card advertises no reachable JSON-RPC url.

    a2a-sdk 1.x clients that rely on card discovery read the reachable endpoint
    from ``supported_interfaces[].url``; an empty one breaks discovery
    (``no compatible transports found``).  The card built by
    :class:`AgentCardBuilder` leaves the url empty because the server does not
    know its own public address -- the deployer should supply it via
    ``TrpcA2aAgentService(rpc_url=...)`` or a custom ``agent_card``.  However,
    JSON-RPC clients that call the endpoint directly never read the card, so a
    missing url must not prevent the server from starting: warn instead of
    raising.

    Args:
        card: The agent card to inspect (a2a-sdk protobuf message).
    """
    if not any(i.url for i in card.supported_interfaces):
        logger.warning("Agent card advertises no reachable url; discovery-based clients "
                       "won't be able to call it. Configure TrpcA2aAgentService("
                       "rpc_url='http://host:port') or pass a custom agent_card whose "
                       "interfaces carry a url.")


def _ensure_v0_3_interface(card: Any) -> None:
    """Advertise a v0.3 JSONRPC interface on the card (in place).

    The a2a-sdk's ``agent_card_to_dict`` only generates the legacy v0.3 card
    (with a top-level ``url``) when at least one interface declares
    ``protocol_version`` <= ``0.3``; without it, a 0.3 client that validates the
    card against the v0.3 pydantic model fails with a missing ``url`` field.
    This appends a ``0.3`` interface that reuses an already-advertised url (so
    the 0.3 and 1.0 interfaces always point at the same endpoint).

    Args:
        card: The agent card to mutate (a2a-sdk protobuf message).
    """
    if any(i.protocol_binding == "JSONRPC" and i.protocol_version == "0.3" for i in card.supported_interfaces):
        return
    # Reuse an already-advertised url so the 0.3 and 1.0 interfaces point at the
    # same endpoint when one exists; otherwise the 0.3 interface inherits the
    # same missing-url state and the warning from ``_ensure_card_has_url``
    # already covers it.
    advertised_url = next(
        (i.url for i in card.supported_interfaces if i.url),
        "",
    )
    card.supported_interfaces.append(
        AgentInterface(
            protocol_binding="JSONRPC",
            protocol_version="0.3",
            url=advertised_url,
        ))
