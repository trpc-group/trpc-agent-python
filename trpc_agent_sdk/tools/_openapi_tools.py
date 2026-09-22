# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Convert OpenAPI 3.x operations into executable agent tools."""

from __future__ import annotations

import copy
import ipaddress
import json
import re
from collections.abc import Iterable
from collections.abc import Mapping
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from typing import Optional
from typing import Union
from urllib.parse import quote
from urllib.parse import urljoin
from urllib.parse import urlparse

import httpx
import yaml
from typing_extensions import override

from trpc_agent_sdk.abc import ToolPredicate
from trpc_agent_sdk.abc import ToolSetABC as BaseToolSet
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.types import FunctionDeclaration

from ._base_tool import BaseTool

_HTTP_METHODS = ("delete", "get", "head", "options", "patch", "post", "put", "trace")
_OPERATION_ID_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
_PARAMETER_LOCATIONS = {"cookie", "header", "path", "query"}


class OpenAPIToolError(RuntimeError):
    """Base exception for OpenAPI tool conversion and execution."""


class OpenAPISpecError(OpenAPIToolError, ValueError):
    """Raised when an OpenAPI document cannot be converted safely."""


class OpenAPIHTTPError(OpenAPIToolError):
    """Raised when an OpenAPI operation returns an HTTP error."""

    def __init__(self, *, operation_id: str, status_code: int, content: Any):
        self.content = content
        self.operation_id = operation_id
        self.status_code = status_code
        super().__init__(f"OpenAPI operation '{operation_id}' returned HTTP {status_code}: {content!r}")


class _LocalRefResolver:
    """Resolve local JSON Pointer references without mutating the document."""

    def __init__(self, document: Mapping[str, Any]):
        self._document = document

    def resolve(self, value: Any, stack: tuple[str, ...] = ()) -> Any:
        if isinstance(value, list):
            return [self.resolve(item, stack) for item in value]
        if not isinstance(value, Mapping):
            return copy.deepcopy(value)

        reference = value.get("$ref")
        if reference is not None:
            if not isinstance(reference, str) or not reference.startswith("#/"):
                raise OpenAPISpecError(f"Only local OpenAPI references are supported: {reference!r}")
            if reference in stack:
                chain = " -> ".join((*stack, reference))
                raise OpenAPISpecError(f"Circular OpenAPI reference is not supported: {chain}")
            target = self._lookup(reference)
            resolved = self.resolve(target, (*stack, reference))
            if not isinstance(resolved, dict):
                raise OpenAPISpecError(f"OpenAPI reference must resolve to an object: {reference}")
            siblings = {key: item for key, item in value.items() if key != "$ref"}
            resolved.update(self.resolve(siblings, stack))
            return resolved

        return {str(key): self.resolve(item, stack) for key, item in value.items()}

    def _lookup(self, reference: str) -> Any:
        current: Any = self._document
        for encoded_part in reference[2:].split("/"):
            part = encoded_part.replace("~1", "/").replace("~0", "~")
            if not isinstance(current, Mapping) or part not in current:
                raise OpenAPISpecError(f"Unresolvable OpenAPI reference: {reference}")
            current = current[part]
        return current


def _validate_path_template(path: Any) -> str:
    if not isinstance(path, str) or not path.startswith("/"):
        raise OpenAPISpecError(f"OpenAPI path must be a path template beginning with '/': {path!r}")
    parsed = urlparse(path)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise OpenAPISpecError(f"Invalid OpenAPI path template: {path!r}")
    return path


def load_openapi_document(source: Union[Mapping[str, Any], str, Path]) -> dict[str, Any]:
    """Load and validate an OpenAPI 3.x document from a mapping or local file."""
    if isinstance(source, Mapping):
        document = copy.deepcopy(dict(source))
    else:
        path = Path(source).expanduser()
        if not path.is_file():
            raise OpenAPISpecError(f"OpenAPI document is not a local file: {path}")
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as error:
            raise OpenAPISpecError(f"Unable to read OpenAPI document {path}: {error}") from error
        document = _parse_document_text(text, path)

    if not isinstance(document, dict):
        raise OpenAPISpecError("OpenAPI document must be a JSON/YAML object")
    version = document.get("openapi")
    if not isinstance(version, str) or not version.startswith("3."):
        raise OpenAPISpecError("Only OpenAPI 3.x documents are supported")
    paths = document.get("paths")
    if not isinstance(paths, Mapping):
        raise OpenAPISpecError("OpenAPI document must contain a 'paths' object")
    for path in paths:
        _validate_path_template(path)
    return document


def _parse_document_text(text: str, path: Path) -> dict[str, Any]:
    try:
        if path.suffix.lower() == ".json":
            value = json.loads(text)
        else:
            value = yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as error:
        raise OpenAPISpecError(f"Invalid OpenAPI document {path}: {error}") from error
    if not isinstance(value, dict):
        raise OpenAPISpecError(f"OpenAPI document {path} must contain an object")
    return value


def _server_url(server: Any) -> Optional[str]:
    if not isinstance(server, Mapping):
        return None
    value = server.get("url")
    if not isinstance(value, str) or not value:
        return None
    variables = server.get("variables", {})
    if not isinstance(variables, Mapping):
        raise OpenAPISpecError("OpenAPI server variables must be an object")
    for name, config in variables.items():
        if not isinstance(config, Mapping) or "default" not in config:
            raise OpenAPISpecError(f"OpenAPI server variable '{name}' requires a default")
        value = value.replace("{" + str(name) + "}", str(config["default"]))
    if "{" in value or "}" in value:
        raise OpenAPISpecError(f"Unresolved OpenAPI server variable in URL: {value}")
    return value


def _select_server_url(*server_groups: Any) -> Optional[str]:
    for servers in server_groups:
        if isinstance(servers, list) and servers:
            value = _server_url(servers[0])
            if value:
                return value
    return None


def _validate_base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise OpenAPISpecError(f"OpenAPI base URL must be an absolute HTTP(S) URL: {value!r}")
    if parsed.username or parsed.password or parsed.fragment:
        raise OpenAPISpecError("OpenAPI base URL must not contain credentials or a fragment")
    return value.rstrip("/")


def _normalize_hostname(value: Any) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise OpenAPISpecError(f"OpenAPI allowed host must be a non-empty hostname string: {value!r}")
    if value.endswith("."):
        value = value[:-1]
    if not value:
        raise OpenAPISpecError("OpenAPI allowed host must not be empty")

    try:
        return ipaddress.ip_address(value).compressed.lower()
    except ValueError:
        pass

    if ":" in value:
        raise OpenAPISpecError(f"OpenAPI allowed host must not include a port: {value!r}")
    try:
        hostname = value.encode("idna").decode("ascii").lower()
    except UnicodeError as error:
        raise OpenAPISpecError(f"Invalid OpenAPI allowed host: {value!r}") from error
    if len(hostname) > 253:
        raise OpenAPISpecError(f"Invalid OpenAPI allowed host: {value!r}")
    labels = hostname.split(".")
    if any(not label or len(label) > 63 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
           for label in labels):
        raise OpenAPISpecError(f"Invalid OpenAPI allowed host: {value!r}")
    if len(labels) > 1 and all(label.isdigit() for label in labels):
        raise OpenAPISpecError(f"Invalid OpenAPI allowed host: {value!r}")
    return hostname


def _normalize_allowed_hosts(allowed_hosts: Optional[Iterable[str]]) -> Optional[frozenset[str]]:
    if allowed_hosts is None:
        return None
    if isinstance(allowed_hosts, str):
        raise OpenAPISpecError("OpenAPI allowed_hosts must be an iterable of hostname strings")
    return frozenset(_normalize_hostname(host) for host in allowed_hosts)


def _normalize_schema(schema: Any) -> dict[str, Any]:
    if not isinstance(schema, Mapping):
        return {}
    normalized = copy.deepcopy(dict(schema))
    nullable = normalized.pop("nullable", False)
    normalized.pop("discriminator", None)
    normalized.pop("example", None)
    normalized.pop("externalDocs", None)
    normalized.pop("readOnly", None)
    normalized.pop("writeOnly", None)
    normalized.pop("xml", None)
    if nullable:
        schema_type = normalized.get("type")
        if isinstance(schema_type, str):
            normalized["type"] = [schema_type, "null"]
    for key in ("allOf", "anyOf", "oneOf"):
        if isinstance(normalized.get(key), list):
            normalized[key] = [_normalize_schema(item) for item in normalized[key]]
    if isinstance(normalized.get("items"), Mapping):
        normalized["items"] = _normalize_schema(normalized["items"])
    if isinstance(normalized.get("additionalProperties"), Mapping):
        normalized["additionalProperties"] = _normalize_schema(normalized["additionalProperties"])
    properties = normalized.get("properties")
    if isinstance(properties, Mapping):
        normalized["properties"] = {str(name): _normalize_schema(item) for name, item in properties.items()}
    return normalized


def _response_content(response: httpx.Response) -> Any:
    if not response.content:
        return None
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type == "application/json" or content_type.endswith("+json"):
        try:
            return response.json()
        except ValueError:
            return response.text
    return response.text


class OpenAPITool(BaseTool):
    """A single executable HTTP operation generated from OpenAPI."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        method: str,
        path: str,
        base_url: str,
        input_schema: dict[str, Any],
        parameters: list[dict[str, Any]],
        request_body_required: bool,
        client: httpx.AsyncClient,
        static_headers: Mapping[str, str],
        allowed_hosts: Optional[frozenset[str]],
        filters_name: Optional[list[str]] = None,
        filters: Optional[list[BaseFilter]] = None,
    ):
        super().__init__(name=name, description=description, filters_name=filters_name, filters=filters)
        self._allowed_hosts = allowed_hosts
        self._base_url = base_url
        self._client = client
        self._input_schema = input_schema
        self._method = method
        self._parameters = parameters
        self._path = path
        self._request_body_required = request_body_required
        self._static_headers = dict(static_headers)

    @property
    def input_schema(self) -> dict[str, Any]:
        """Return a defensive copy of the generated JSON input schema."""
        return copy.deepcopy(self._input_schema)

    @override
    def _get_declaration(self) -> FunctionDeclaration:
        return FunctionDeclaration(
            description=self.description,
            name=self.name,
            parameters_json_schema=self.input_schema,
        )

    @override
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        del tool_context
        return await self.call(args)

    async def call(self, args: Mapping[str, Any]) -> dict[str, Any]:
        """Execute the HTTP operation directly, without an agent context."""
        path = self._path
        query: list[tuple[str, Any]] = []
        headers: dict[str, str] = {}
        cookies = SimpleCookie()

        for parameter in self._parameters:
            name = parameter["name"]
            location = parameter["in"]
            if name not in args:
                if parameter.get("required"):
                    raise OpenAPIToolError(f"Missing required argument '{name}' for operation '{self.name}'")
                continue
            value = args[name]
            if location == "path":
                path = path.replace("{" + name + "}", quote(str(value), safe=""))
            elif location == "query":
                if isinstance(value, list):
                    query.extend((name, item) for item in value)
                else:
                    query.append((name, value))
            elif location == "header":
                headers[name] = str(value)
            elif location == "cookie":
                cookies[name] = str(value)

        unresolved = re.findall(r"\{[^{}]+\}", path)
        if unresolved:
            raise OpenAPIToolError(f"Unresolved path parameters for operation '{self.name}': {unresolved}")

        if cookies:
            headers["Cookie"] = cookies.output(header="", sep=";").strip()
        headers.update(self._static_headers)
        request_kwargs: dict[str, Any] = {
            "headers": headers,
            "params": query,
        }
        if "request_body" in args:
            request_kwargs["json"] = args["request_body"]
        elif self._request_body_required:
            raise OpenAPIToolError(f"Missing required argument 'request_body' for operation '{self.name}'")

        url = urljoin(self._base_url + "/", path.lstrip("/"))
        raw_host = urlparse(url).hostname
        host = _normalize_hostname(raw_host)
        if self._allowed_hosts is not None and host not in self._allowed_hosts:
            raise OpenAPIToolError(f"Host '{host}' is not allowed for operation '{self.name}'")

        try:
            response = await self._client.request(self._method, url, **request_kwargs)
        except httpx.HTTPError as error:
            raise OpenAPIToolError(f"OpenAPI operation '{self.name}' request failed: {error}") from error

        content = _response_content(response)
        if response.is_error:
            raise OpenAPIHTTPError(operation_id=self.name, status_code=response.status_code, content=content)
        return {
            "content": content,
            "content_type": response.headers.get("content-type", ""),
            "status_code": response.status_code,
        }


class OpenAPIToolSet(BaseToolSet):
    """A first-class toolset generated from an OpenAPI 3.x document."""

    def __init__(
        self,
        source: Union[Mapping[str, Any], str, Path],
        *,
        base_url: Optional[str] = None,
        operation_ids: Optional[Iterable[str]] = None,
        static_headers: Optional[Mapping[str, str]] = None,
        auth: Optional[httpx.Auth] = None,
        timeout: Union[float, httpx.Timeout] = 30.0,
        client: Optional[httpx.AsyncClient] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        allowed_hosts: Optional[Iterable[str]] = None,
        tool_filter: Optional[Union[ToolPredicate, list[str]]] = None,
        is_include_all_tools: bool = True,
        name: str = "openapi",
    ):
        super().__init__(tool_filter=tool_filter, is_include_all_tools=is_include_all_tools, name=name)
        if client is not None and transport is not None:
            raise OpenAPISpecError("Provide either client or transport, not both")
        self.document = load_openapi_document(source)
        self._allowed_hosts = _normalize_allowed_hosts(allowed_hosts)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(auth=auth, timeout=timeout, transport=transport)
        self._static_headers = dict(static_headers or {})
        self._base_url_override = _validate_base_url(base_url) if base_url else None
        self._tools = self._build_tools(operation_ids)

    @property
    def tools(self) -> tuple[OpenAPITool, ...]:
        """Return all converted operations before context filtering."""
        return tuple(self._tools)

    @override
    async def get_tools(self, invocation_context: Optional[InvocationContext] = None) -> list[OpenAPITool]:
        return [tool for tool in self._tools if self._is_tool_selected(tool, invocation_context)]

    @override
    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _build_tools(self, operation_ids: Optional[Iterable[str]]) -> list[OpenAPITool]:
        selected = set(operation_ids) if operation_ids is not None else None
        resolver = _LocalRefResolver(self.document)
        operations: list[tuple[str, str, str, Mapping[str, Any], Mapping[str, Any]]] = []
        seen: set[str] = set()

        for path, raw_path_item in self.document["paths"].items():
            if not isinstance(raw_path_item, Mapping):
                raise OpenAPISpecError("OpenAPI paths must map path strings to objects")
            path_item = (resolver.resolve(raw_path_item) if "$ref" in raw_path_item else dict(raw_path_item))
            for method in _HTTP_METHODS:
                raw_operation = path_item.get(method)
                if raw_operation is None:
                    continue
                if not isinstance(raw_operation, Mapping):
                    raise OpenAPISpecError(f"OpenAPI operation {method.upper()} {path} must be an object")
                operation = (resolver.resolve(raw_operation) if "$ref" in raw_operation else dict(raw_operation))
                operation_id = operation.get("operationId")
                self._validate_operation_id(operation_id, method, path, seen)
                seen.add(operation_id)
                operations.append((operation_id, method, path, path_item, operation))

        if selected is not None:
            missing = selected - seen
            if missing:
                raise OpenAPISpecError(f"Unknown OpenAPI operationId values: {sorted(missing)}")

        tools = []
        for operation_id, method, path, path_item, operation in operations:
            if selected is not None and operation_id not in selected:
                continue
            tools.append(self._build_tool(resolver, operation_id, method, path, path_item, operation))
        return tools

    @staticmethod
    def _validate_operation_id(operation_id: Any, method: str, path: str, seen: set[str]) -> None:
        location = f"{method.upper()} {path}"
        if not isinstance(operation_id, str) or not operation_id:
            raise OpenAPISpecError(f"OpenAPI operation {location} is missing operationId")
        if not _OPERATION_ID_PATTERN.fullmatch(operation_id):
            raise OpenAPISpecError(
                f"Unsafe OpenAPI operationId {operation_id!r} at {location}; "
                "use 1-64 letters, digits, underscores, or hyphens, starting with a letter or underscore")
        if operation_id in seen:
            raise OpenAPISpecError(f"Duplicate OpenAPI operationId: {operation_id!r}")

    def _build_tool(
        self,
        resolver: _LocalRefResolver,
        operation_id: str,
        method: str,
        path: str,
        path_item: Mapping[str, Any],
        operation: Mapping[str, Any],
    ) -> OpenAPITool:
        parameters = self._collect_parameters(resolver, path_item, operation, operation_id)
        properties = {parameter["name"]: _normalize_schema(parameter.get("schema", {})) for parameter in parameters}
        required = [parameter["name"] for parameter in parameters if parameter.get("required")]
        body_required = False
        raw_body = operation.get("requestBody")
        if raw_body is not None:
            body = resolver.resolve(raw_body)
            content = body.get("content", {}) if isinstance(body, Mapping) else {}
            media_type = content.get("application/json") if isinstance(content, Mapping) else None
            if not isinstance(media_type, Mapping) or not isinstance(media_type.get("schema"), Mapping):
                raise OpenAPISpecError(f"Operation '{operation_id}' requestBody must define an application/json schema")
            if "request_body" in properties:
                raise OpenAPISpecError(
                    f"Operation '{operation_id}' parameter name 'request_body' conflicts with requestBody")
            properties["request_body"] = _normalize_schema(resolver.resolve(media_type["schema"]))
            body_required = bool(body.get("required"))
            if body_required:
                required.append("request_body")

        input_schema: dict[str, Any] = {
            "additionalProperties": False,
            "properties": properties,
            "type": "object",
        }
        if required:
            input_schema["required"] = required

        raw_base_url = self._base_url_override or _select_server_url(
            operation.get("servers"),
            path_item.get("servers"),
            self.document.get("servers"),
        )
        if not raw_base_url:
            raise OpenAPISpecError(
                f"Operation '{operation_id}' has no server URL; provide base_url or an OpenAPI servers entry")
        base_url = _validate_base_url(raw_base_url)
        description = operation.get("description") or operation.get("summary") or f"{method.upper()} {path}"
        return OpenAPITool(
            allowed_hosts=self._allowed_hosts,
            base_url=base_url,
            client=self._client,
            description=str(description),
            input_schema=input_schema,
            method=method.upper(),
            name=operation_id,
            parameters=parameters,
            path=path,
            request_body_required=body_required,
            static_headers=self._static_headers,
        )

    @staticmethod
    def _collect_parameters(
        resolver: _LocalRefResolver,
        path_item: Mapping[str, Any],
        operation: Mapping[str, Any],
        operation_id: str,
    ) -> list[dict[str, Any]]:
        combined: dict[tuple[str, str], dict[str, Any]] = {}
        for raw_parameters in (path_item.get("parameters", []), operation.get("parameters", [])):
            if not isinstance(raw_parameters, list):
                raise OpenAPISpecError(f"Operation '{operation_id}' parameters must be a list")
            for raw_parameter in raw_parameters:
                parameter = resolver.resolve(raw_parameter)
                if not isinstance(parameter, dict):
                    raise OpenAPISpecError(f"Operation '{operation_id}' parameter must be an object")
                name = parameter.get("name")
                location = parameter.get("in")
                if not isinstance(name, str) or not name:
                    raise OpenAPISpecError(f"Operation '{operation_id}' has a parameter without a name")
                if location not in _PARAMETER_LOCATIONS:
                    raise OpenAPISpecError(
                        f"Operation '{operation_id}' parameter '{name}' has unsupported location {location!r}")
                if not isinstance(parameter.get("schema"), Mapping):
                    raise OpenAPISpecError(f"Operation '{operation_id}' parameter '{name}' must define a schema")
                if location == "path" and not parameter.get("required"):
                    raise OpenAPISpecError(f"Operation '{operation_id}' path parameter '{name}' must be required")
                combined[(name, location)] = parameter

        by_name: dict[str, str] = {}
        for name, location in combined:
            existing = by_name.setdefault(name, location)
            if existing != location:
                raise OpenAPISpecError(
                    f"Operation '{operation_id}' uses parameter '{name}' in both {existing} and {location}")
        return list(combined.values())
