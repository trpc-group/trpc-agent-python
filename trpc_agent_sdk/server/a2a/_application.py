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
from a2a.types import AgentCard
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
        a2a_svc: The initialized :class:`TrpcA2aAgentService` to serve.  Call
            ``a2a_svc.initialize()`` first (or pass ``agent_card=`` when
            constructing the service).  The card may leave
            ``supported_interfaces[].url`` empty (built without
            ``TrpcA2aAgentService(rpc_url=...)``); this is logged as a warning
            so discovery-based clients fail loudly, while JSON-RPC calls that
            do not use the card are unaffected.  When ``enable_v0_3_compat``
            is True a missing url is a ``ValueError`` instead: 0.3 discovery
            cannot work without a top-level ``url``.
        enable_v0_3_compat: Whether to accept legacy v0.3 clients on the same
            endpoint (see the a2a-sdk migration guide).  When ``True``, also
            publishes the card at ``/.well-known/agent.json`` (the path 0.3
            ``A2ACardResolver`` fetches by default).  When ``False`` (the
            default), only ``/.well-known/agent-card.json`` is served and
            ``agent.json`` returns 404 — 0.3 clients cannot discover the
            service.  ``True`` also requires a reachable interface url
            (otherwise ``ValueError``: an empty url cannot be turned into a
            usable 0.3 top-level ``url``).  The service's ``agent_card``
            is not mutated; a copy is used for well-known routes (and, when
            this function constructs the default handler, for that handler).
            Passing a custom ``request_handler`` does **not** append a 0.3
            interface to that handler's own ``agent_card`` — the caller must
            advertise ``protocol_version="0.3"`` on it if the handler (or
            anything else) inspects ``supported_interfaces``.
        request_handler: Optional fully-customized A2A request handler (advanced
            usage).  When provided it is used as-is, giving full control over the
            handler's configuration (task store, push notifications, extended
            cards, ...).  ``enable_v0_3_compat`` still enables 0.3 JSON-RPC
            decoding and still patches the well-known card copy so 0.3 clients
            can discover a top-level ``url``; it does not rewrite the custom
            handler's ``agent_card``.  Defaults to a
            ``DefaultRequestHandler`` built from ``a2a_svc`` with an in-memory
            task store.

    Returns:
        A Starlette application wired with the agent-card and JSON-RPC routes.

    Raises:
        ValueError: If ``a2a_svc.agent_card`` is ``None`` (service not
            initialized and no card was supplied).  Also raised when
            ``enable_v0_3_compat`` is True and no interface advertises a url
            (0.3 card discovery cannot invent a reachable endpoint).
    """
    from starlette.applications import Starlette

    if a2a_svc.agent_card is None:
        raise ValueError("a2a_svc.agent_card is None; call TrpcA2aAgentService.initialize() "
                         "first (or pass agent_card= when constructing the service).")

    card = a2a_svc.agent_card
    if enable_v0_3_compat:
        card = AgentCard()
        card.CopyFrom(a2a_svc.agent_card)
        _ensure_v0_3_interface(card)

    request_handler = request_handler or DefaultRequestHandler(
        agent_executor=a2a_svc,
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    _ensure_card_has_url(card, required=enable_v0_3_compat)
    routes: list[Any] = []
    routes.extend(create_agent_card_routes(card))
    if enable_v0_3_compat:
        routes.extend(create_agent_card_routes(card, card_url="/.well-known/agent.json"))
    routes.extend(
        create_jsonrpc_routes(
            request_handler,
            rpc_url=_jsonrpc_path_from_card(card),
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
    Query strings and fragments are ignored.  An empty path (including
    ``http://host?q=1``) normalizes to ``/``.  A trailing slash on a non-root
    path is stripped so ``/a2a/`` and ``/a2a`` mount the same endpoint.

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

    # Empty path (bare origin, or origin plus query/fragment only) is `/`.
    path = urlparse(advertised).path or "/"
    if not path.startswith("/"):
        return "/"
    return path.rstrip("/") or "/"


def _ensure_card_has_url(card: Any, *, required: bool = False) -> None:
    """Require or warn if the card advertises no reachable JSON-RPC url.

    a2a-sdk 1.x clients that rely on card discovery read the reachable endpoint
    from ``supported_interfaces[].url``; an empty one breaks discovery
    (``no compatible transports found``).  The card built by
    :class:`AgentCardBuilder` leaves the url empty because the server does not
    know its own public address -- the deployer should supply it via
    ``TrpcA2aAgentService(rpc_url=...)`` or a custom ``agent_card``.  However,
    JSON-RPC clients that call the endpoint directly never read the card, so a
    missing url must not prevent a 1.0-only server from starting: warn instead
    of raising.

    ``enable_v0_3_compat`` is different: 0.3 clients discover via the well-known
    card's top-level ``url``, which is generated from the 0.3 interface url.
    Reusing an empty 1.0 url does not give them a usable endpoint, so a missing
    url is a configuration error (``required=True``).

    Args:
        card: The agent card to inspect (a2a-sdk protobuf message).
        required: When True, raise ``ValueError`` instead of logging a warning.

    Raises:
        ValueError: If ``required`` is True and no interface advertises a url.
    """
    if any(i.url for i in card.supported_interfaces):
        return
    hint = ("Configure TrpcA2aAgentService(rpc_url='http://host:port') or pass a "
            "custom agent_card whose interfaces carry a url.")
    if required:
        raise ValueError("enable_v0_3_compat requires a reachable interface url "
                         f"so 0.3 clients can discover the service. {hint}")
    logger.warning("Agent card advertises no reachable url; discovery-based clients "
                   f"won't be able to call it. {hint}")


def _ensure_v0_3_interface(card: Any) -> None:
    """Advertise a v0.3 JSONRPC interface on the card (in place).

    The a2a-sdk's ``agent_card_to_dict`` only generates the legacy v0.3 card
    (with a top-level ``url``) when at least one interface declares
    ``protocol_version`` <= ``0.3``; without it, a 0.3 client that validates the
    card against the v0.3 pydantic model fails with a missing ``url`` field.
    This appends a ``0.3`` interface that reuses an already-advertised url (so
    the 0.3 and 1.0 interfaces always point at the same endpoint).  An empty
    url is not invented here; ``create_a2a_application`` rejects that case
    when compat is on.

    Args:
        card: The agent card to mutate (a2a-sdk protobuf message).
    """
    if any(i.protocol_binding == "JSONRPC" and i.protocol_version == "0.3" for i in card.supported_interfaces):
        return
    # Reuse an already-advertised url so the 0.3 and 1.0 interfaces point at the
    # same endpoint when one exists; otherwise the 0.3 interface inherits the
    # empty url.  ``create_a2a_application`` then raises when compat is on.
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
