# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from trpc_agent_sdk.tools import OpenAPIHTTPError
from trpc_agent_sdk.tools import OpenAPISpecError
from trpc_agent_sdk.tools import OpenAPIToolError
from trpc_agent_sdk.tools import OpenAPIToolSet
from trpc_agent_sdk.tools import load_openapi_document


def _document(paths: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "info": {
            "title": "Test API",
            "version": "1.0.0",
        },
        "openapi": "3.0.3",
        "paths": paths,
        "servers": [{
            "url": "https://api.example.test/v1",
        }],
        **extra,
    }


def _operation(operation_id: str = "get_item", **extra: Any) -> dict[str, Any]:
    return {
        "operationId": operation_id,
        "responses": {
            "200": {
                "description": "OK",
            },
        },
        "summary": "Get an item",
        **extra,
    }


async def _run(tool: Any, args: dict[str, Any]) -> Any:
    return await tool._run_async_impl(tool_context=None, args=args)


@pytest.mark.asyncio
async def test_builds_schema_from_parameters_body_and_local_refs():
    document = _document(
        {
            "/items/{item_id}": {
                "get":
                _operation(
                    parameters=[
                        {
                            "$ref": "#/components/parameters/ItemId",
                        },
                        {
                            "in": "query",
                            "name": "limit",
                            "schema": {
                                "$ref": "#/components/schemas/Limit",
                            },
                        },
                    ],
                    requestBody={
                        "$ref": "#/components/requestBodies/UpdateBody",
                    },
                ),
            },
        },
        components={
            "parameters": {
                "ItemId": {
                    "in": "path",
                    "name": "item_id",
                    "required": True,
                    "schema": {
                        "type": "string",
                    },
                },
            },
            "requestBodies": {
                "UpdateBody": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "$ref": "#/components/schemas/Item",
                            },
                        },
                    },
                    "required": True,
                },
            },
            "schemas": {
                "Item": {
                    "properties": {
                        "name": {
                            "type": "string",
                        },
                    },
                    "required": ["name"],
                    "type": "object",
                },
                "Limit": {
                    "maximum": 100,
                    "type": "integer",
                },
            },
        },
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
    toolset = OpenAPIToolSet(document, client=client)
    tool = toolset.tools[0]

    assert tool.name == "get_item"
    assert tool.input_schema == {
        "additionalProperties": False,
        "properties": {
            "item_id": {
                "type": "string",
            },
            "limit": {
                "maximum": 100,
                "type": "integer",
            },
            "request_body": {
                "properties": {
                    "name": {
                        "type": "string",
                    },
                },
                "required": ["name"],
                "type": "object",
            },
        },
        "required": ["item_id", "request_body"],
        "type": "object",
    }
    declaration = tool._get_declaration()
    assert declaration.parameters_json_schema == tool.input_schema
    await client.aclose()


@pytest.mark.asyncio
async def test_executes_all_parameter_locations_json_body_headers_auth_and_override():
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(201, headers={"content-type": "application/json"}, json={"saved": True})

    parameters = [
        {
            "in": "path",
            "name": "item_id",
            "required": True,
            "schema": {
                "type": "string",
            },
        },
        {
            "in": "query",
            "name": "tag",
            "schema": {
                "items": {
                    "type": "string",
                },
                "type": "array",
            },
        },
        {
            "in": "header",
            "name": "X-Request-ID",
            "required": True,
            "schema": {
                "type": "string",
            },
        },
        {
            "in": "cookie",
            "name": "session",
            "schema": {
                "type": "string",
            },
        },
    ]
    document = _document({
        "/items/{item_id}": {
            "post":
            _operation(
                operation_id="save_item",
                parameters=parameters,
                requestBody={
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                            },
                        },
                    },
                    "required": True,
                },
            ),
        },
    })
    toolset = OpenAPIToolSet(
        document,
        allowed_hosts=["mock.local"],
        auth=httpx.BasicAuth("demo", "secret"),
        base_url="https://mock.local/api",
        static_headers={
            "X-API-Key": "fixed",
            "X-Request-ID": "trusted",
        },
        timeout=4.5,
        transport=httpx.MockTransport(handler),
    )

    result = await _run(
        toolset.tools[0],
        {
            "X-Request-ID": "model-value",
            "item_id": "a/b",
            "request_body": {
                "name": "desk",
            },
            "session": "cookie-value",
            "tag": ["red", "blue"],
        },
    )

    assert result == {
        "content": {
            "saved": True,
        },
        "content_type": "application/json",
        "status_code": 201,
    }
    request = captured[0]
    assert request.method == "POST"
    assert request.url.raw_path.split(b"?", 1)[0] == b"/api/items/a%2Fb"
    assert request.url.params.get_list("tag") == ["red", "blue"]
    assert request.headers["authorization"].startswith("Basic ")
    assert request.headers["x-api-key"] == "fixed"
    assert request.headers["x-request-id"] == "trusted"
    assert request.headers["cookie"] == "session=cookie-value"
    assert json.loads(request.content) == {
        "name": "desk",
    }
    assert request.extensions["timeout"]["read"] == 4.5
    await toolset.close()


@pytest.mark.asyncio
async def test_operation_filtering_and_standard_tool_filter():
    document = _document({
        "/first": {
            "get": _operation("first"),
        },
        "/second": {
            "get": _operation("second"),
        },
    })
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))

    toolset = OpenAPIToolSet(document, client=client, operation_ids=["second"])
    assert [tool.name for tool in toolset.tools] == ["second"]

    filtered = OpenAPIToolSet(
        document,
        client=client,
        is_include_all_tools=False,
        tool_filter=["first"],
    )
    assert [tool.name for tool in await filtered.get_tools()] == ["first"]
    await client.aclose()


@pytest.mark.asyncio
async def test_non_json_response_and_empty_response_are_parsed():

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/text"):
            return httpx.Response(200, headers={"content-type": "text/plain; charset=utf-8"}, text="hello")
        return httpx.Response(204)

    document = _document({
        "/empty": {
            "delete": _operation("delete_item"),
        },
        "/text": {
            "get": _operation("get_text"),
        },
    })
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    toolset = OpenAPIToolSet(document, client=client)

    text_result = await _run(next(tool for tool in toolset.tools if tool.name == "get_text"), {})
    empty_result = await _run(next(tool for tool in toolset.tools if tool.name == "delete_item"), {})

    assert text_result["content"] == "hello"
    assert text_result["content_type"].startswith("text/plain")
    assert empty_result["content"] is None
    assert empty_result["status_code"] == 204
    await client.aclose()


@pytest.mark.asyncio
async def test_http_and_transport_errors_are_clear():
    document = _document({
        "/items": {
            "get": _operation(),
        },
    })
    error_client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(404, headers={"content-type": "application/json"}, json={"error": "gone"})))
    toolset = OpenAPIToolSet(document, client=error_client)

    with pytest.raises(OpenAPIHTTPError, match="HTTP 404") as error_info:
        await _run(toolset.tools[0], {})
    assert error_info.value.content == {
        "error": "gone",
    }
    assert error_info.value.status_code == 404
    await error_client.aclose()

    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    transport_client = httpx.AsyncClient(transport=httpx.MockTransport(fail))
    transport_toolset = OpenAPIToolSet(document, client=transport_client)
    with pytest.raises(OpenAPIToolError, match="request failed: offline"):
        await _run(transport_toolset.tools[0], {})
    await transport_client.aclose()


@pytest.mark.asyncio
async def test_required_arguments_and_host_allowlist_are_enforced():
    document = _document({
        "/items/{item_id}": {
            "get":
            _operation(parameters=[
                {
                    "in": "path",
                    "name": "item_id",
                    "required": True,
                    "schema": {
                        "type": "string",
                    },
                },
            ], ),
        },
    })
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    toolset = OpenAPIToolSet(document, allowed_hosts=["other.example"], client=client)

    with pytest.raises(OpenAPIToolError, match="Missing required argument 'item_id'"):
        await _run(toolset.tools[0], {})
    with pytest.raises(OpenAPIToolError, match="Host 'api.example.test' is not allowed"):
        await _run(toolset.tools[0], {
            "item_id": "1",
        })
    await client.aclose()


@pytest.mark.asyncio
async def test_allowed_hosts_are_case_insensitive_and_normalized():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200)

    toolset = OpenAPIToolSet(
        _document({
            "/items": {
                "get": _operation(),
            },
        }),
        allowed_hosts=["API.EXAMPLE.TEST."],
        transport=httpx.MockTransport(handler),
    )

    await _run(toolset.tools[0], {})

    assert len(requests) == 1
    await toolset.close()


@pytest.mark.parametrize(
    "allowed_hosts",
    [
        "api.example.test",
        ["https://api.example.test"],
        ["api.example.test:443"],
        ["bad_host.example"],
        [" api.example.test"],
    ],
)
def test_rejects_invalid_allowed_hosts(allowed_hosts: Any):
    with pytest.raises(OpenAPISpecError, match="allowed host|allowed_hosts"):
        OpenAPIToolSet(_document({}), allowed_hosts=allowed_hosts)


@pytest.mark.parametrize(
    "path",
    [
        "https://evil.example/items",
        "items",
        "//evil.example/items",
        "/items?redirect=https://evil.example",
        "/items#fragment",
    ],
)
def test_rejects_invalid_path_keys_before_any_request(path: str):
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200)

    with pytest.raises(OpenAPISpecError, match="path template"):
        OpenAPIToolSet(
            _document({
                path: {
                    "get": _operation(),
                },
            }),
            transport=httpx.MockTransport(handler),
        )

    assert request_count == 0


@pytest.mark.parametrize(
    ("paths", "message"),
    [
        (
            {
                "/missing": {
                    "get": {
                        "responses": {},
                    },
                },
            },
            "missing operationId",
        ),
        (
            {
                "/unsafe": {
                    "get": _operation("../unsafe"),
                },
            },
            "Unsafe OpenAPI operationId",
        ),
        (
            {
                "/first": {
                    "get": _operation("duplicate"),
                },
                "/second": {
                    "post": _operation("duplicate"),
                },
            },
            "Duplicate OpenAPI operationId",
        ),
    ],
)
def test_rejects_missing_unsafe_and_duplicate_operation_ids(paths: dict[str, Any], message: str):
    with pytest.raises(OpenAPISpecError, match=message):
        OpenAPIToolSet(_document(paths))


def test_rejects_unknown_operation_filter_and_parameter_name_collision():
    document = _document({
        "/items": {
            "get":
            _operation(parameters=[
                {
                    "in": "query",
                    "name": "token",
                    "schema": {
                        "type": "string",
                    },
                },
                {
                    "in": "header",
                    "name": "token",
                    "schema": {
                        "type": "string",
                    },
                },
            ], ),
        },
    })
    with pytest.raises(OpenAPISpecError, match="Unknown OpenAPI operationId"):
        OpenAPIToolSet(document, operation_ids=["absent"])
    with pytest.raises(OpenAPISpecError, match="both query and header"):
        OpenAPIToolSet(document)


def test_loads_mapping_json_and_yaml_sources(tmp_path: Path):
    document = _document({})
    json_path = tmp_path / "openapi.json"
    yaml_path = tmp_path / "openapi.yaml"
    json_path.write_text(json.dumps(document), encoding="utf-8")
    yaml_path.write_text(yaml.safe_dump(document), encoding="utf-8")

    assert load_openapi_document(document) == document
    assert load_openapi_document(json_path) == document
    assert load_openapi_document(yaml_path) == document


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("https://example.com/openapi.yaml", "not a local file"),
        ({
            "openapi": "2.0",
            "paths": {}
        }, "Only OpenAPI 3.x"),
        ({
            "openapi": "3.1.0"
        }, "must contain a 'paths' object"),
    ],
)
def test_rejects_remote_or_invalid_sources(source: Any, message: str):
    with pytest.raises(OpenAPISpecError, match=message):
        load_openapi_document(source)


def test_rejects_remote_and_circular_refs():
    remote = _document({
        "/items": {
            "get": _operation(parameters=[{
                "$ref": "https://example.com/parameters.yaml#/Item",
            }]),
        },
    })
    with pytest.raises(OpenAPISpecError, match="Only local OpenAPI references"):
        OpenAPIToolSet(remote)

    circular = _document(
        {
            "/items": {
                "get": _operation(parameters=[{
                    "$ref": "#/components/parameters/Loop",
                }]),
            },
        },
        components={
            "parameters": {
                "Loop": {
                    "$ref": "#/components/parameters/Loop",
                },
            },
        },
    )
    with pytest.raises(OpenAPISpecError, match="Circular OpenAPI reference"):
        OpenAPIToolSet(circular)
