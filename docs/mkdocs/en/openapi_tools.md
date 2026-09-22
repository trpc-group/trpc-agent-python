# OpenAPI Tools

`OpenAPIToolSet` converts operations in an OpenAPI 3.x document into standard tRPC-Agent `BaseTool` instances. It loads a Python mapping or local JSON/YAML file, derives each tool name from `operationId`, exposes JSON input schemas to the model, and executes calls asynchronously with `httpx`.

## Use cases

OpenAPI Tools are useful when a system already exposes REST APIs and maintains
an OpenAPI document, but implementing a separate `FunctionTool` for every
operation would duplicate existing contracts. Typical examples include:

- CRM customer search and activity records;
- ticket lookup, creation, and status updates;
- product, inventory, and order services;
- internal approval, CMDB, and operations platforms;
- SaaS products that publish OpenAPI specifications.

The feature reuses the existing API contract:

```text
OpenAPI document
→ operationId and parameter schemas
→ Agent-visible Tools
→ model selects a Tool and generates arguments
→ asynchronous HTTP request
→ Tool Result returned to the model
```

It does not replace the Agent. `OpenAPIToolSet` adapts APIs into Tools, while
`LlmAgent` still interprets user intent, chooses operations, prepares arguments,
and explains responses.

## Basic usage

```python
from trpc_agent_sdk.tools import OpenAPIToolSet

toolset = OpenAPIToolSet(
    "openapi.yaml",
    base_url="https://api.example.com/v1",
    operation_ids=["get_pet", "create_pet"],
    static_headers={"Authorization": "Bearer secret"},
    allowed_hosts=["api.example.com"],
    timeout=20.0,
)

tools = await toolset.get_tools()
```

Pass `toolset` directly in an agent's `tools` list. Call `await toolset.close()` if no runner owns its lifecycle. An existing `httpx.AsyncClient` can be supplied with `client`; when supplied, the caller retains ownership. `transport` is also available for an owned custom transport such as `httpx.MockTransport`.

`auth` accepts an `httpx.Auth` implementation. `static_headers` are applied after operation arguments, so trusted application headers cannot be replaced by model-generated header parameters.

## Conversion logic

For every HTTP operation:

1. `operationId` becomes the tool name. Missing, duplicate, or unsafe IDs are rejected.
2. Path-level and operation-level parameters are combined. Operation-level entries override matching `(name, location)` entries.
3. Path, query, header, and cookie parameters become top-level tool arguments.
4. an `application/json` request body becomes the `request_body` argument.
5. Required parameters and request bodies populate the generated JSON Schema `required` list.
6. Local `$ref` JSON Pointers are resolved before schema generation.
7. The base URL is chosen from `base_url`, operation servers, path servers, then document servers.

Successful calls return:

```python
{
    "status_code": 200,
    "content_type": "application/json",
    "content": {"id": "pet-1"},
}
```

JSON media types, including `+json`, are decoded. Other responses are returned as text and empty responses as `None`. HTTP 4xx/5xx responses raise `OpenAPIHTTPError`; transport, argument, and conversion failures use clear `OpenAPIToolError` or `OpenAPISpecError` exceptions.

## Selection and filtering

Use `operation_ids` to select a fixed subset while converting. Unknown requested IDs are rejected. The standard `tool_filter` and `is_include_all_tools` arguments remain available for context-time filtering through the normal toolset interface.

## Security and SSRF

Treat OpenAPI documents as executable configuration:

- Prefer checked-in local specifications. Remote specification URL loading is intentionally not supported; fetch and validate a trusted document in application code before passing a mapping.
- Set `base_url` from trusted configuration instead of accepting it from users.
- Set `allowed_hosts` to constrain the final request host. Redirect behavior remains controlled by the supplied `httpx.AsyncClient`; the default client does not follow redirects.
- Inject credentials with `auth` or `static_headers`, and do not place secrets in the OpenAPI file.
- Review operations before exposing them to a model, especially destructive methods.
- Apply network egress controls as the final SSRF boundary. Host allowlists do not replace DNS and network-layer controls.

Base URLs must be absolute HTTP(S) URLs and cannot contain embedded credentials or fragments.

## Supported subset and limitations

Supported:

- OpenAPI 3.x documents from Python mappings and local JSON/YAML files
- HTTP operations with safe, unique `operationId` values
- Path, query, header, and cookie parameters
- JSON request bodies
- Local, non-circular `$ref` references
- server variable defaults
- JSON, text, and empty response bodies

Not currently supported:

- Swagger/OpenAPI 2.x
- remote or external `$ref` references and remote specification loading
- circular schemas
- non-JSON request bodies, multipart uploads, callbacks, links, or webhooks
- full OpenAPI parameter serialization styles such as `deepObject`
- automatic interpretation of OpenAPI `securitySchemes`
- response-schema validation

## Complete Agent example

The [OpenAPI tools example](../../../examples/openapi_tools/README.md) follows
the same complete Agent structure as Quickstart:

```text
root_agent (LlmAgent)
└── OpenAPIToolSet
    ├── get_pet
    └── create_pet
```

A real model decides whether to call `get_pet` or `create_pet`, and the example
prints generated arguments, Tool Results, and final Agent responses. The Pet
API itself uses an in-process `httpx.MockTransport`, so no external business
service is contacted.

Copy and configure the environment before running:

```bash
cd examples/openapi_tools
cp .env.example .env
python run_agent.py
```

For production, replace the Mock Transport with the real HTTP API and use
`operation_ids` to expose only reviewed operations.
