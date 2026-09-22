# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Create deterministic OpenAPI tools backed by an in-process mock API."""

import json
from pathlib import Path

import httpx

from trpc_agent_sdk.tools import OpenAPIToolSet


def _mock_api(request: httpx.Request) -> httpx.Response:
    if request.method == "GET" and request.url.path == "/v1/pets/pet-1":
        return httpx.Response(
            200,
            json={
                "id": "pet-1",
                "name": "Mochi",
                "species": "cat",
            },
        )
    if request.method == "POST" and request.url.path == "/v1/pets":
        body = json.loads(request.content)
        return httpx.Response(
            201,
            json={
                "id": "pet-2",
                **body,
            },
        )
    return httpx.Response(404, json={
        "error": "not found",
    })


def create_toolset() -> OpenAPIToolSet:
    """Load the local specification and attach an in-process HTTP transport."""
    specification = Path(__file__).parents[1] / "openapi.yaml"
    return OpenAPIToolSet(
        specification,
        allowed_hosts=["pets.local"],
        static_headers={
            "X-Demo-Token": "local-only",
        },
        transport=httpx.MockTransport(_mock_api),
    )
