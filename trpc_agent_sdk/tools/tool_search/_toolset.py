# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Deferred tool discovery and loading."""

from __future__ import annotations

import asyncio
import hashlib
import re
import threading
from dataclasses import dataclass
from typing import Any
from typing import Optional
from typing import Sequence
from typing_extensions import override

from trpc_agent_sdk.abc import ToolSetABC as BaseToolSet
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type

from .._base_tool import BaseTool
from .._long_running_tool import LongRunningFunctionTool
from ._semantic import SemanticToolIndex
from ._types import InvocationMode
from ._types import MCPToolbox
from ._types import Toolbox
from ._types import ToolSearchDocument
from ._types import ToolSearchEmbeddingUsage
from ._types import ToolSearchOptions

TOOL_SEARCH_PLACEHOLDER = "{deferred_tools_section}"
TOOL_SEARCH_TOOL_NAME = "tool_search"
CALL_TOOL_NAME = "call_tool"

_DEFAULT_NAMESPACE = ""
_MAX_REQUEST_RESULTS = 10
_EXACT_NAME_SCORE = 1000
_QUERY_SEPARATOR = re.compile(r"[^\w\-+]+", re.UNICODE)
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

_SEARCH_DESCRIPTION = """Load deferred tools from the tool catalog so they become callable.
After loading a tool, call it directly and do not search for it again.

- Known name: copy the exact name shown in the catalog into tool_names.
- A namespace is only a search filter; never prepend it to a tool name
  (use "get_forecast", not "weather.get_forecast").
- Unknown name: use queries and the matching namespace.
- Leave namespace empty only when no catalog domain fits."""

_DISPATCH_SEARCH_DESCRIPTION = """Load deferred tools from the tool catalog, then invoke them
with call_tool. Search results include input_schema for constructing call_tool params.

- Known name: copy the exact name shown in the catalog into tool_names.
- A namespace is only a search filter; never prepend it to a tool name
  (use "get_forecast", not "weather.get_forecast").
- Unknown name: use queries and the matching namespace.
- Leave namespace empty only when no catalog domain fits."""

_CALL_DESCRIPTION = """Invoke a deferred tool previously loaded by tool_search.
Use the exact tool_name returned by tool_search and params matching input_schema."""


@dataclass(frozen=True)
class _ToolEntry:
    name: str
    description: str
    namespace: Optional[str]
    tool: BaseTool
    deferred: bool
    declaration: FunctionDeclaration


@dataclass(frozen=True)
class _Snapshot:
    entries: dict[str, _ToolEntry]
    canonical_names: dict[str, str]
    deferred_names: frozenset[str]
    namespace_tools: dict[str, frozenset[str]]
    namespace_descriptions: dict[str, str]
    namespace_order: tuple[str, ...]


class _RenamedTool(BaseTool):
    """Expose a dynamic tool under an MCP-style globally unique name."""

    def __init__(self, original: BaseTool, name: str) -> None:
        BaseTool.__init__(
            self,
            name=name,
            description=original.description,
            filters=list(original.filters),
        )
        self._original = original

    @property
    @override
    def is_streaming(self) -> bool:
        return self._original.is_streaming

    @property
    @override
    def is_progress_streaming(self) -> bool:
        return self._original.is_progress_streaming

    @property
    @override
    def api_variant(self) -> str:
        return self._original.api_variant

    @property
    def skip_summarization(self) -> bool:
        """Preserve execution behavior of wrapped dynamic tools."""
        return bool(getattr(self._original, "skip_summarization", False))

    @override
    def _get_declaration(self) -> Optional[FunctionDeclaration]:
        declaration = self._original._get_declaration()  # pylint: disable=protected-access
        if declaration is None:
            return None
        return declaration.model_copy(
            update={"name": self.name},
            deep=True,
        )

    @override
    async def _run_async_impl(
        self,
        *,
        tool_context: InvocationContext,
        args: dict[str, Any],
    ) -> Any:
        return await self._original._run_async_impl(  # pylint: disable=protected-access
            tool_context=tool_context,
            args=args,
        )

    async def run_streaming(
        self,
        *,
        tool_context: InvocationContext,
        args: dict[str, Any],
    ):
        run_streaming = getattr(self._original, "run_streaming", None)
        if run_streaming is None:
            raise TypeError(f"Tool {self._original.name!r} does not support progress streaming", )
        async for value in run_streaming(
                tool_context=tool_context,
                args=args,
        ):
            yield value


class _RenamedLongRunningTool(_RenamedTool, LongRunningFunctionTool):
    """Preserve the framework's long-running marker after namespace."""


class _ToolSearchTool(BaseTool):

    def __init__(
        self,
        owner: "ToolSearchToolSet",
        snapshot: _Snapshot,
        allowed: Optional[dict[str, bool]],
        catalog: str,
    ) -> None:
        description = owner._search_description
        if owner.options.catalog_in_description and catalog:
            description = f"{description}\n\n{catalog}"
        super().__init__(
            name=TOOL_SEARCH_TOOL_NAME,
            description=description,
        )
        self._owner = owner
        self._snapshot = snapshot
        self._allowed = allowed
        self._catalog = catalog

    @override
    def _get_declaration(self) -> FunctionDeclaration:
        string_array = Schema(
            type=Type.ARRAY,
            items=Schema(type=Type.STRING),
        )
        return FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "tool_names":
                    Schema(
                        type=Type.ARRAY,
                        items=Schema(type=Type.STRING),
                        description=("Exact names copied from the catalog. Namespace is "
                                     "not part of a tool name: use 'get_forecast', not "
                                     "'weather.get_forecast'."),
                    ),
                    "queries":
                    string_array.model_copy(update={
                        "description": ("Keyword or semantic queries. Prefix a keyword "
                                        "with '+' to require it in keyword mode.")
                    }, ),
                    "namespace":
                    Schema(
                        type=Type.STRING,
                        description=("Toolbox namespace. Leave empty to search "
                                     "all deferred tools."),
                    ),
                    "max_results":
                    Schema(
                        type=Type.INTEGER,
                        description="Maximum loaded matches (request cap: 10).",
                    ),
                },
            ),
        )

    @override
    async def process_request(
        self,
        *,
        tool_context: InvocationContext,
        llm_request: LlmRequest,
    ) -> None:
        self._owner._reset_embedding_usage(tool_context)
        await super().process_request(
            tool_context=tool_context,
            llm_request=llm_request,
        )
        if self._owner.options.catalog_in_description:
            _replace_catalog_placeholder(llm_request, "")
        else:
            _replace_catalog_placeholder(llm_request, self._catalog)

    @override
    async def _run_async_impl(
        self,
        *,
        tool_context: InvocationContext,
        args: dict[str, Any],
    ) -> Any:
        return await self._owner._search(
            tool_context,
            self._snapshot,
            self._allowed,
            args,
        )


class _CallTool(BaseTool):

    def __init__(
        self,
        owner: "ToolSearchToolSet",
        snapshot: _Snapshot,
        allowed: Optional[dict[str, bool]],
    ) -> None:
        super().__init__(
            name=CALL_TOOL_NAME,
            description=_CALL_DESCRIPTION,
        )
        self._owner = owner
        self._snapshot = snapshot
        self._allowed = allowed

    @override
    def _get_declaration(self) -> FunctionDeclaration:
        return FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters=Schema(
                type=Type.OBJECT,
                required=["tool_name", "params"],
                properties={
                    "tool_name":
                    Schema(
                        type=Type.STRING,
                        description="Exact deferred tool name returned by tool_search.",
                    ),
                    "params":
                    Schema(
                        type=Type.OBJECT,
                        description="Arguments matching the returned input_schema.",
                        additional_properties=True,
                    ),
                },
            ),
        )

    @override
    async def _run_async_impl(
        self,
        *,
        tool_context: InvocationContext,
        args: dict[str, Any],
    ) -> Any:
        tool_name = str(args.get("tool_name", "")).strip()
        params = args.get("params")
        if not tool_name:
            return {
                "status": "error",
                "message": "tool_name is required.",
            }
        if not isinstance(params, dict):
            return {
                "status": "error",
                "message": "params must be an object.",
            }
        entry = _entry_by_name(self._snapshot, tool_name)
        if entry is None:
            return {
                "status": "error",
                "message": f"Unknown tool {tool_name!r}. Use tool_search to find it.",
            }
        if entry.deferred:
            if self._allowed is not None and not self._allowed.get(entry.name, False):
                return {
                    "status": "error",
                    "message": f"You do not have permission to use {entry.name!r}.",
                }
            loaded = self._owner._load_discovered(tool_context)
            if entry.name not in loaded:
                return {
                    "status":
                    "error",
                    "message": (f"Tool {entry.name!r} is not loaded yet. Call tool_search "
                                f"with tool_names=[{entry.name!r}] first."),
                }
        if entry.tool.is_progress_streaming:
            chunks: list[Any] = []
            async for value in entry.tool.run_streaming(
                    tool_context=tool_context,
                    args=params,
            ):
                chunks.append(value)
            return {"chunks": chunks}
        return await entry.tool.run_async(
            tool_context=tool_context,
            args=params,
        )


class _UnloadedTool(BaseTool):
    """Declaration-free guard for a Native call made before discovery."""

    def __init__(self, name: str) -> None:
        super().__init__(
            name=name,
            description="Deferred tool loading guard.",
        )

    @override
    async def _run_async_impl(
        self,
        *,
        tool_context: InvocationContext,
        args: dict[str, Any],
    ) -> str:
        del tool_context, args
        return (f"Tool {self.name!r} is not loaded yet. Call tool_search "
                f"with tool_names=[{self.name!r}] first, then retry this call.")


class ToolSearchToolSet(BaseToolSet):
    """Expose a large tool catalog through on-demand discovery.

    Preset tools are always visible. Deferred tools are searchable but become
    visible only after ``tool_search`` records them in session state. Dispatch
    mode keeps the model-facing surface fixed at ``tool_search`` and
    ``call_tool``.
    """

    def __init__(
        self,
        *,
        preset_tools: Optional[Sequence[BaseTool]] = None,
        deferred_tools: Optional[Sequence[BaseTool]] = None,
        toolboxes: Optional[Sequence[Toolbox]] = None,
        mcp_toolboxes: Optional[Sequence[MCPToolbox]] = None,
        options: Optional[ToolSearchOptions] = None,
        semantic_index: Optional[SemanticToolIndex] = None,
        name: str = "tool_search_tools",
    ) -> None:
        super().__init__(name=name)
        self.options = options or ToolSearchOptions()
        if self.options.max_results <= 0:
            raise ValueError("max_results must be greater than zero")
        self._semantic_index = semantic_index
        self._preset_tools = tuple(preset_tools or ())
        self._deferred_tools = tuple(deferred_tools or ())
        self._toolboxes = tuple(toolboxes or ())
        self._mcp_toolboxes = tuple(mcp_toolboxes or ())
        self._normalize_configuration()
        self._dynamic_cache: dict[str, tuple[BaseTool, ...]] = {}
        self._dynamic_fingerprint_cache: dict[str, dict[str, str]] = {}
        self._dynamic_tool_names: set[str] = set()
        self._dynamic_cache_lock = threading.Lock()
        self._usage_lock = threading.Lock()
        self._usage_metadata_key = f"_tool_search_embedding_usage:{self.name}:{id(self)}"
        self._dynamic_refresh_locks = {box.server_name.strip(): asyncio.Lock() for box in self._mcp_toolboxes}

    @property
    def _search_description(self) -> str:
        if self.options.invocation_mode == InvocationMode.DISPATCH:
            return _DISPATCH_SEARCH_DESCRIPTION
        return _SEARCH_DESCRIPTION

    @override
    async def get_tools(
        self,
        invocation_context: Optional[InvocationContext] = None,
    ) -> list[BaseTool]:
        snapshot = await self._build_snapshot(invocation_context)
        allowed = self._permissions(invocation_context, snapshot)
        catalog = _render_catalog(snapshot, allowed, self.options.invocation_mode)
        tools: list[BaseTool] = [entry.tool for entry in snapshot.entries.values() if not entry.deferred]
        tools.append(_ToolSearchTool(self, snapshot, allowed, catalog))

        if self.options.invocation_mode == InvocationMode.DISPATCH:
            tools.append(_CallTool(self, snapshot, allowed))
            return _deduplicate_tools(tools)

        if invocation_context is not None:
            for name in self._load_discovered(invocation_context):
                entry = snapshot.entries.get(name)
                if entry is None or not entry.deferred:
                    continue
                if allowed is not None and not allowed.get(name, False):
                    continue
                tools.append(entry.tool)
        return _deduplicate_tools(tools)

    @override
    async def get_unresolved_tool(
        self,
        name: str,
        invocation_context: Optional[InvocationContext] = None,
    ) -> Optional[BaseTool]:
        if (self.options.invocation_mode != InvocationMode.NATIVE or invocation_context is None):
            return None
        snapshot = await self._build_snapshot(invocation_context)
        entry = _entry_by_name(snapshot, name)
        if entry is None:
            namespace = _mcp_namespace(name)
            if namespace is not None and any(box.server_name.strip() == namespace for box in self._mcp_toolboxes):
                return _UnloadedTool(name)
            return None
        if not entry.deferred:
            return None
        allowed = self._permissions(invocation_context, snapshot)
        if allowed is not None and not allowed.get(entry.name, False):
            return None
        if entry.name in self._load_discovered(invocation_context):
            return None
        return _UnloadedTool(entry.name)

    @override
    async def close(self) -> None:
        closed: set[int] = set()
        for box in self._mcp_toolboxes:
            if id(box.toolset) in closed:
                continue
            closed.add(id(box.toolset))
            await box.toolset.close()

    async def _build_snapshot(
        self,
        ctx: Optional[InvocationContext],
    ) -> _Snapshot:
        entries: dict[str, _ToolEntry] = {}
        namespace_tools: dict[str, set[str]] = {}
        descriptions: dict[str, str] = {}
        order: list[str] = []

        for tool in self._preset_tools:
            _add_entry(entries, tool, namespace=None, deferred=False)

        if self._deferred_tools:
            namespace_tools[_DEFAULT_NAMESPACE] = set()
            order.append(_DEFAULT_NAMESPACE)
            for tool in self._deferred_tools:
                entry = _add_entry(
                    entries,
                    tool,
                    namespace=_DEFAULT_NAMESPACE,
                    deferred=True,
                )
                if entry is not None:
                    namespace_tools[_DEFAULT_NAMESPACE].add(entry.name)

        for box in self._toolboxes:
            namespace = box.name.strip()
            descriptions[namespace] = box.description.strip()
            namespace_tools[namespace] = set()
            order.append(namespace)
            for tool in box.tools:
                entry = _add_entry(
                    entries,
                    tool,
                    namespace=namespace,
                    deferred=True,
                )
                if entry is not None:
                    namespace_tools[namespace].add(entry.name)

        for box in self._mcp_toolboxes:
            namespace = box.server_name.strip()
            descriptions[namespace] = box.description.strip()
            namespace_tools[namespace] = set()
            order.append(namespace)
            cache_key = f"mcp:{namespace}"
            forget_names: tuple[str, ...] = ()
            async with self._dynamic_refresh_locks[namespace]:
                try:
                    dynamic = await box.toolset.get_tools(ctx)
                except Exception:  # pylint: disable=broad-except
                    with self._dynamic_cache_lock:
                        wrapped = self._dynamic_cache.get(cache_key, ())
                    if not wrapped:
                        raise
                    logger.warning(
                        "Dynamic tool listing failed for %s; using the last "
                        "nonempty snapshot",
                        namespace,
                        exc_info=True,
                    )
                else:
                    if dynamic:
                        wrapped = self._normalize_dynamic_tools(
                            dynamic,
                            namespace,
                        )
                        fresh_fingerprints = {tool.name: _tool_fingerprint(tool) for tool in wrapped}
                        with self._dynamic_cache_lock:
                            previous_fingerprints = self._dynamic_fingerprint_cache.get(
                                cache_key,
                                {},
                            )
                            changed = {
                                name
                                for name, fingerprint in fresh_fingerprints.items()
                                if (name in previous_fingerprints and previous_fingerprints[name] != fingerprint)
                            }
                            removed = set(previous_fingerprints) - set(fresh_fingerprints)
                            self._dynamic_cache[cache_key] = wrapped
                            self._dynamic_fingerprint_cache[cache_key] = fresh_fingerprints
                            self._dynamic_tool_names.update(previous_fingerprints)
                            self._dynamic_tool_names.update(fresh_fingerprints)
                            forget_names = tuple(sorted(changed | removed))
                    else:
                        with self._dynamic_cache_lock:
                            wrapped = self._dynamic_cache.get(cache_key, ())
                if self._semantic_index is not None and forget_names:
                    await self._semantic_index.forget(forget_names)
            for tool in wrapped:
                entry = _add_entry(
                    entries,
                    tool,
                    namespace=namespace,
                    deferred=True,
                )
                if entry is not None:
                    namespace_tools[namespace].add(entry.name)

        return _Snapshot(
            entries=entries,
            canonical_names={name.lower(): name
                             for name in entries},
            deferred_names=frozenset(name for name, entry in entries.items() if entry.deferred),
            namespace_tools={
                name: frozenset(tools)
                for name, tools in namespace_tools.items()
            },
            namespace_descriptions=descriptions,
            namespace_order=tuple(order),
        )

    def _normalize_configuration(self) -> None:
        reserved = {TOOL_SEARCH_TOOL_NAME, CALL_TOOL_NAME}
        strict = self.options.strict_validation
        seen_tools: dict[str, tuple[BaseTool, str, str]] = {}

        def accept_tool(
            tool: Any,
            owner: str,
        ) -> bool:
            if not isinstance(tool, BaseTool):
                if strict:
                    raise ValueError(f"Invalid tool in {owner}: {tool!r}")
                logger.warning(
                    "Skipping invalid tool in namespace %r: %r",
                    owner,
                    tool,
                )
                return False
            if tool.name in reserved:
                raise ValueError(f"Tool name {tool.name!r} is reserved")
            declaration = tool._get_declaration()  # pylint: disable=protected-access
            if declaration is None:
                logger.warning(
                    "Skipping tool %r in namespace %r because it has no declaration",
                    tool.name,
                    owner,
                )
                return False
            fingerprint = _tool_fingerprint(tool)
            previous = seen_tools.get(tool.name)
            if previous is None:
                seen_tools[tool.name] = (tool, fingerprint, owner)
                return True
            if strict:
                raise ValueError(f"Duplicate tool name: {tool.name!r}")
            _, previous_fingerprint, previous_owner = previous
            if previous_fingerprint != fingerprint:
                raise ValueError(
                    f"Conflicting declarations for tool {tool.name!r} "
                    f"in {previous_owner!r} and {owner!r}", )
            logger.warning(
                "Tool %r is already registered in namespace %r; keeping "
                "the first owner and ignoring namespace %r",
                tool.name,
                previous_owner,
                owner,
            )
            return False

        preset_tools = tuple(tool for tool in self._preset_tools if accept_tool(tool, "preset"))
        deferred_tools = tuple(tool for tool in self._deferred_tools if accept_tool(tool, _DEFAULT_NAMESPACE))

        toolbox_data: dict[str, tuple[str, list[BaseTool]]] = {}
        toolbox_order: list[str] = []
        for box in self._toolboxes:
            namespace = box.name.strip()
            if not namespace:
                if strict:
                    raise ValueError("Toolbox name must not be empty")
                logger.warning(
                    "Skipping toolbox with empty name: description=%r",
                    box.description,
                )
                continue
            existing = toolbox_data.get(namespace)
            if existing is not None and strict:
                raise ValueError(f"Duplicate toolbox namespace: {namespace!r}", )
            if existing is None:
                description = box.description.strip()
                tools: list[BaseTool] = []
                toolbox_data[namespace] = (description, tools)
                toolbox_order.append(namespace)
            else:
                description, tools = existing
                incoming_description = box.description.strip()
                if not description and incoming_description:
                    toolbox_data[namespace] = (
                        incoming_description,
                        tools,
                    )
                logger.warning(
                    "Merging duplicate toolbox namespace %r",
                    namespace,
                )
            for tool in box.tools:
                if accept_tool(tool, namespace):
                    tools.append(tool)

        normalized_toolboxes = tuple(
            Toolbox(
                name=namespace,
                description=toolbox_data[namespace][0],
                tools=tuple(toolbox_data[namespace][1]),
            ) for namespace in toolbox_order)

        namespaces = set(toolbox_order)
        normalized_mcp: list[MCPToolbox] = []
        for box in self._mcp_toolboxes:
            namespace = box.server_name.strip()
            if not namespace:
                if strict:
                    raise ValueError("MCP server_name must not be empty")
                logger.warning(
                    "Skipping MCP toolbox with empty server_name: description=%r",
                    box.description,
                )
                continue
            if box.toolset is None:
                if strict:
                    raise ValueError(f"MCP toolbox {namespace!r} must have a toolset", )
                logger.warning(
                    "Skipping MCP toolbox %r because toolset is None",
                    namespace,
                )
                continue
            if namespace in namespaces:
                if strict:
                    raise ValueError(f"Duplicate toolbox namespace: {namespace!r}", )
                logger.warning(
                    "Skipping MCP toolbox %r because its namespace is "
                    "already registered",
                    namespace,
                )
                continue
            namespaces.add(namespace)
            normalized_mcp.append(
                MCPToolbox(
                    server_name=namespace,
                    description=box.description.strip(),
                    toolset=box.toolset,
                ))

        self._preset_tools = preset_tools
        self._deferred_tools = deferred_tools
        self._toolboxes = normalized_toolboxes
        self._mcp_toolboxes = tuple(normalized_mcp)

    def _normalize_dynamic_tools(
        self,
        tools: Sequence[Any],
        namespace: str,
    ) -> tuple[BaseTool, ...]:
        wrapped_by_name: dict[str, BaseTool] = {}
        fingerprints: dict[str, str] = {}
        for tool in tools:
            if not isinstance(tool, BaseTool):
                if self.options.strict_validation:
                    raise ValueError(f"Invalid dynamic tool in {namespace!r}: {tool!r}", )
                logger.warning(
                    "Skipping invalid dynamic tool in namespace %r: %r",
                    namespace,
                    tool,
                )
                continue
            declaration = tool._get_declaration()  # pylint: disable=protected-access
            if declaration is None:
                logger.warning(
                    "Skipping dynamic tool %r in namespace %r because it "
                    "has no declaration",
                    tool.name,
                    namespace,
                )
                continue
            wrapped = _rename_dynamic_tool(
                tool,
                f"mcp__{namespace}__{tool.name}",
            )
            fingerprint = _tool_fingerprint(wrapped)
            previous = wrapped_by_name.get(wrapped.name)
            if previous is None:
                wrapped_by_name[wrapped.name] = wrapped
                fingerprints[wrapped.name] = fingerprint
                continue
            if self.options.strict_validation:
                raise ValueError(f"Duplicate dynamic tool name: {wrapped.name!r}", )
            if fingerprints[wrapped.name] != fingerprint:
                raise ValueError(f"Conflicting declarations for dynamic tool "
                                 f"{wrapped.name!r}", )
            logger.warning(
                "Dynamic tool %r is listed more than once by namespace %r; "
                "keeping the first entry",
                wrapped.name,
                namespace,
            )
        return tuple(wrapped_by_name.values())

    def _permissions(
        self,
        ctx: Optional[InvocationContext],
        snapshot: _Snapshot,
    ) -> Optional[dict[str, bool]]:
        permission_filter = self.options.permission_filter
        if permission_filter is None or ctx is None:
            return None
        names = sorted(snapshot.deferred_names)
        result = permission_filter(ctx, names)
        return {name: bool(result.get(name, False)) for name in names}

    async def _search(
        self,
        ctx: InvocationContext,
        snapshot: _Snapshot,
        allowed: Optional[dict[str, bool]],
        args: dict[str, Any],
    ) -> dict[str, Any]:
        tool_names = _string_list(args.get("tool_names"))
        queries = _string_list(args.get("queries"))
        namespace = str(args.get("namespace", "")).strip()
        requested_max = _positive_int(args.get("max_results"))
        max_results = min(requested_max, _MAX_REQUEST_RESULTS) if requested_max else self.options.max_results

        if tool_names:
            selected = _select_by_name(snapshot, tool_names, allowed)
            overflow: list[str] = []
        else:
            error = _validate_namespace(snapshot, namespace)
            if error is not None:
                return error
            if namespace and not queries:
                ranked = _namespace_names(snapshot, namespace, allowed)
                selected, overflow = _split_results(ranked, max_results)
            elif queries:
                try:
                    selected, overflow = await self._search_queries(
                        ctx,
                        snapshot,
                        allowed,
                        namespace,
                        queries,
                        max_results,
                    )
                except Exception as exc:  # pylint: disable=broad-except
                    if not self.options.embedding_fail_open or self._semantic_index is None:
                        raise
                    logger.warning(
                        "Semantic tool search failed; falling back to keyword search: %s",
                        exc,
                    )
                    ranked = _keyword_search(
                        snapshot,
                        namespace,
                        queries,
                        allowed,
                    )
                    selected, overflow = _split_results(ranked, max_results)
            else:
                selected, overflow = [], []

        previously_loaded = set(self._load_discovered(ctx))
        deferred_selected = [name for name in selected if name in snapshot.deferred_names]
        self._append_discovered(ctx, deferred_selected)
        return _format_result(
            snapshot,
            selected,
            overflow,
            previously_loaded,
            self.options.invocation_mode,
        )

    async def _search_queries(
        self,
        ctx: InvocationContext,
        snapshot: _Snapshot,
        allowed: Optional[dict[str, bool]],
        namespace: str,
        queries: list[str],
        max_results: int,
    ) -> tuple[list[str], list[str]]:
        if self._semantic_index is None:
            ranked = _keyword_search(
                snapshot,
                namespace,
                queries,
                allowed,
            )
            return _split_results(ranked, max_results)

        candidates = _candidate_names(
            snapshot,
            namespace,
            allowed,
        )
        documents = [_document_for(snapshot.entries[name]) for name in candidates]
        best_rank: dict[str, int] = {}
        usage = ToolSearchEmbeddingUsage()
        try:
            for query in queries:
                ranked = await self._semantic_index.search(
                    query,
                    documents,
                    len(documents),
                    verifier=self._semantic_candidate_is_current,
                    usage=usage,
                )
                for rank, name in enumerate(ranked):
                    if name not in best_rank or rank < best_rank[name]:
                        best_rank[name] = rank
        finally:
            self._record_embedding_usage(ctx, usage)
        merged = sorted(best_rank, key=lambda name: (best_rank[name], name))
        return _split_results(merged, max_results)

    def get_embedding_usage(
        self,
        ctx: InvocationContext,
        *,
        reset: bool = False,
    ) -> Optional[ToolSearchEmbeddingUsage]:
        """Return embedding usage accumulated for this invocation context."""
        if self._semantic_index is None:
            return None
        with self._usage_lock:
            usage = ctx.agent_context.get_metadata(self._usage_metadata_key)
            if not isinstance(usage, ToolSearchEmbeddingUsage):
                usage = ToolSearchEmbeddingUsage()
                ctx.agent_context.with_metadata(self._usage_metadata_key, usage)
            snapshot = usage.copy()
            if reset:
                ctx.agent_context.with_metadata(
                    self._usage_metadata_key,
                    ToolSearchEmbeddingUsage(),
                )
            return snapshot

    def _record_embedding_usage(
        self,
        ctx: InvocationContext,
        usage: ToolSearchEmbeddingUsage,
    ) -> None:
        if usage.prompt_tokens == 0 and usage.total_tokens == 0:
            return
        with self._usage_lock:
            accumulated = ctx.agent_context.get_metadata(self._usage_metadata_key)
            if not isinstance(accumulated, ToolSearchEmbeddingUsage):
                accumulated = ToolSearchEmbeddingUsage()
                ctx.agent_context.with_metadata(
                    self._usage_metadata_key,
                    accumulated,
                )
            accumulated.add(usage)

    def _reset_embedding_usage(
        self,
        ctx: InvocationContext,
    ) -> None:
        if self._semantic_index is None:
            return
        with self._usage_lock:
            ctx.agent_context.with_metadata(
                self._usage_metadata_key,
                ToolSearchEmbeddingUsage(),
            )

    def _semantic_candidate_is_current(
        self,
        name: str,
        fingerprint: str,
    ) -> bool:
        with self._dynamic_cache_lock:
            if name not in self._dynamic_tool_names:
                return True
            return any(
                fingerprints.get(name) == fingerprint for fingerprints in self._dynamic_fingerprint_cache.values())

    def _state_prefix(self, ctx: InvocationContext) -> str:
        scope = ctx.branch or ctx.agent_name
        return (f"{self.options.state_key_prefix}:{self.name}:{scope}:"
                "discovered_tools:")

    def _load_discovered(self, ctx: InvocationContext) -> list[str]:
        prefix = self._state_prefix(ctx)
        return sorted(key[len(prefix):] for key, value in ctx.state.to_dict().items()
                      if key.startswith(prefix) and value is True)

    def _append_discovered(
        self,
        ctx: InvocationContext,
        names: Sequence[str],
    ) -> None:
        prefix = self._state_prefix(ctx)
        # One state key per tool makes concurrent Event deltas commutative:
        # persisting one search cannot overwrite tools loaded by another.
        for name in names:
            ctx.state[f"{prefix}{name}"] = True


def _rename_dynamic_tool(tool: BaseTool, name: str) -> BaseTool:
    if isinstance(tool, LongRunningFunctionTool):
        return _RenamedLongRunningTool(tool, name)
    return _RenamedTool(tool, name)


def _add_entry(
    entries: dict[str, _ToolEntry],
    tool: BaseTool,
    *,
    namespace: Optional[str],
    deferred: bool,
) -> Optional[_ToolEntry]:
    declaration = tool._get_declaration()  # pylint: disable=protected-access
    if declaration is None:
        logger.warning("Skipping tool %r because it has no declaration", tool.name)
        return None
    if tool.name in entries:
        raise ValueError(f"Duplicate tool name: {tool.name!r}")
    entry = _ToolEntry(
        name=tool.name,
        description=declaration.description or tool.description or "",
        namespace=namespace,
        tool=tool,
        deferred=deferred,
        declaration=declaration,
    )
    entries[tool.name] = entry
    return entry


def _entry_by_name(snapshot: _Snapshot, name: str) -> Optional[_ToolEntry]:
    canonical = snapshot.canonical_names.get(name.lower())
    return snapshot.entries.get(canonical) if canonical else None


def _mcp_namespace(name: str) -> Optional[str]:
    if not name.startswith("mcp__"):
        return None
    namespace, separator, tool_name = name[len("mcp__"):].partition("__")
    if not separator or not namespace or not tool_name:
        return None
    return namespace


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _positive_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _select_by_name(
    snapshot: _Snapshot,
    names: Sequence[str],
    allowed: Optional[dict[str, bool]],
) -> list[str]:
    selected: list[str] = []
    seen: set[str] = set()
    for name in names:
        entry = _entry_by_name(snapshot, name)
        if entry is None:
            # Models sometimes infer ``namespace.tool_name`` from a grouped
            # catalog even though namespace is only a search filter. Accept
            # that form only when both parts match the real catalog entry.
            namespace, separator, candidate = name.rpartition(".")
            if separator and namespace and candidate:
                qualified_entry = _entry_by_name(snapshot, candidate)
                if (qualified_entry is not None and qualified_entry.namespace is not None
                        and qualified_entry.namespace.lower() == namespace.lower()):
                    entry = qualified_entry
        if entry is None or entry.name in seen:
            continue
        if entry.deferred and allowed is not None and not allowed.get(entry.name, False):
            continue
        selected.append(entry.name)
        seen.add(entry.name)
    return selected


def _validate_namespace(
    snapshot: _Snapshot,
    namespace: str,
) -> Optional[dict[str, Any]]:
    if not namespace or namespace in snapshot.namespace_tools:
        return None
    return {
        "status":
        "unknown_namespace",
        "message":
        f"Namespace {namespace!r} is not in the tool catalog.",
        "namespace":
        namespace,
        "catalog": [{
            "name": name,
            "description": snapshot.namespace_descriptions.get(name, ""),
        } for name in snapshot.namespace_order if name],
    }


def _namespace_names(
    snapshot: _Snapshot,
    namespace: str,
    allowed: Optional[dict[str, bool]],
) -> list[str]:
    return sorted(name for name in snapshot.namespace_tools.get(namespace, ())
                  if allowed is None or allowed.get(name, False))


def _candidate_names(
    snapshot: _Snapshot,
    namespace: str,
    allowed: Optional[dict[str, bool]],
) -> list[str]:
    names = (snapshot.namespace_tools.get(namespace, ()) if namespace else snapshot.deferred_names)
    return sorted(name for name in names if allowed is None or allowed.get(name, False))


def _keyword_search(
    snapshot: _Snapshot,
    namespace: str,
    queries: Sequence[str],
    allowed: Optional[dict[str, bool]],
) -> list[str]:
    candidates = _candidate_names(snapshot, namespace, allowed)
    best: dict[str, int] = {}
    namespace_bias = (_namespace_bias(snapshot, queries) if not namespace else {})
    for query in queries:
        query = query.strip()
        if not query:
            continue
        exact = snapshot.canonical_names.get(query.lower())
        if exact in candidates:
            best[exact] = max(best.get(exact, 0), _EXACT_NAME_SCORE)
            continue
        terms = _tokenize(query)
        required = [term[1:] for term in terms if term.startswith("+")]
        optional = [term for term in terms if not term.startswith("+")]
        scoring_terms = [part for term in required for part in _split_compound(term)] + optional
        for name in candidates:
            entry = snapshot.entries[name]
            metadata = _search_metadata(entry)
            if required and not all(
                    all(_has_term(metadata, part) for part in _split_compound(term)) for term in required):
                continue
            score = _score_metadata(metadata, scoring_terms)
            if score > 0:
                score += namespace_bias.get(entry.namespace or "", 0)
                best[name] = max(best.get(name, 0), score)
    return sorted(
        best,
        key=lambda name: (
            -best[name],
            len(_search_metadata(snapshot.entries[name])["parts"]),
            name,
        ),
    )


def _tokenize(text: str) -> list[str]:
    return [part for part in _QUERY_SEPARATOR.split(text.lower()) if part]


def _split_compound(term: str) -> list[str]:
    parts = [part for part in re.split(r"[_-]+", term.lower()) if part]
    return parts or [term.lower()]


def _search_metadata(entry: _ToolEntry) -> dict[str, Any]:
    expanded = _CAMEL_BOUNDARY.sub(" ", entry.name).replace("_", " ")
    parts = [part for part in expanded.lower().split() if part]
    return {
        "name": entry.name.lower(),
        "parts": parts,
        "full": " ".join(parts),
        "description": entry.description.lower(),
    }


def _has_term(metadata: dict[str, Any], term: str) -> bool:
    term = term.lower()
    return term in metadata["parts"] or _text_has_term(metadata["description"], term)


def _score_metadata(metadata: dict[str, Any], terms: Sequence[str]) -> int:
    score = 0
    for term in terms:
        term = term.lower()
        if ("_" in term or "-" in term) and term in metadata["name"]:
            score += 10
        if term in metadata["parts"]:
            score += 10
        if _text_has_term(metadata["description"], term):
            score += 3
    if score == 0:
        score = sum(3 for term in terms if _text_has_term(metadata["full"], term.lower()))
    return score


def _text_has_term(text: str, term: str) -> bool:
    if term.isascii():
        return re.search(
            rf"(?<!\w){re.escape(term)}(?!\w)",
            text,
            flags=re.IGNORECASE,
        ) is not None
    return term in text


def _namespace_bias(
    snapshot: _Snapshot,
    queries: Sequence[str],
) -> dict[str, int]:
    terms = {term.removeprefix("+") for query in queries for term in _tokenize(query) if term.removeprefix("+")}
    scores: dict[str, int] = {}
    for namespace, description in snapshot.namespace_descriptions.items():
        hits = sum(1 for term in terms if _text_has_term(description.lower(), term))
        if hits:
            scores[namespace] = hits
    return scores


def _split_results(
    names: Sequence[str],
    max_results: int,
) -> tuple[list[str], list[str]]:
    if max_results > 0 and len(names) > max_results:
        return list(names[:max_results]), list(names[max_results:])
    return list(names), []


def _document_for(entry: _ToolEntry) -> ToolSearchDocument:
    text = _document_text(
        entry.name,
        entry.description,
        entry.declaration,
    )
    return ToolSearchDocument(
        name=entry.name,
        description=entry.description,
        namespace=entry.namespace or "",
        text=text,
        fingerprint=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def _tool_fingerprint(tool: BaseTool) -> str:
    declaration = tool._get_declaration()  # pylint: disable=protected-access
    if declaration is None:
        return ""
    text = _document_text(
        tool.name,
        declaration.description or tool.description or "",
        declaration,
    )
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _document_text(
    name: str,
    description: str,
    declaration: FunctionDeclaration,
) -> str:
    declaration_json = declaration.model_dump_json(
        exclude_none=True,
        by_alias=True,
    )
    return (f"Tool: {name}\n"
            f"Description: {description}\n"
            f"Declaration: {declaration_json}")


def _format_result(
    snapshot: _Snapshot,
    selected: Sequence[str],
    overflow: Sequence[str],
    previously_loaded: set[str],
    mode: InvocationMode,
) -> dict[str, Any]:
    tools: list[dict[str, Any]] = []
    any_already_loaded = False
    all_already_loaded = bool(selected)
    for name in selected:
        entry = snapshot.entries[name]
        item: dict[str, Any] = {
            "name": name,
            "description": entry.description,
        }
        if name in previously_loaded:
            item["already_loaded"] = True
            any_already_loaded = True
        else:
            all_already_loaded = False
        if mode == InvocationMode.DISPATCH:
            item["input_schema"] = _input_schema(entry.declaration)
        tools.append(item)

    if not tools:
        status = "No matches. Try different queries, namespace, or tool_names."
    elif mode == InvocationMode.DISPATCH:
        if all_already_loaded:
            status = ("All matches were loaded earlier. Invoke them with "
                      "call_tool using input_schema.")
        elif any_already_loaded:
            status = ("Tools loaded. Invoke them with call_tool using "
                      "input_schema; entries with already_loaded=true were "
                      "loaded earlier.")
        else:
            status = "Tools loaded. Invoke them with call_tool using input_schema."
    else:
        if all_already_loaded:
            status = "All matches were loaded earlier. Call them directly."
        elif any_already_loaded:
            status = ("Tools loaded. Entries with already_loaded=true were "
                      "loaded earlier. Call them directly.")
        else:
            status = "Tools loaded. Call them directly."
    if overflow:
        status += (" More matches are listed in additional_candidates; load one "
                   "with tool_names instead of searching again.")
    result: dict[str, Any] = {
        "status": status,
        "tools": tools,
    }
    if overflow:
        result["additional_candidates"] = list(overflow)
    return result


def _input_schema(declaration: FunctionDeclaration) -> dict[str, Any]:
    if declaration.parameters_json_schema:
        return _normalize_json_schema(dict(declaration.parameters_json_schema))
    if declaration.parameters is None:
        return {}
    return _normalize_json_schema(declaration.parameters.model_dump(
        mode="json",
        exclude_none=True,
        by_alias=True,
    ))


def _normalize_json_schema(value: Any) -> Any:
    """Normalize Google schema enum values to JSON Schema type names."""
    if isinstance(value, dict):
        normalized = {}
        for key, item in value.items():
            if key == "type" and isinstance(item, str) and item in {
                    "ARRAY",
                    "BOOLEAN",
                    "INTEGER",
                    "NUMBER",
                    "OBJECT",
                    "STRING",
            }:
                normalized[key] = item.lower()
            else:
                normalized[key] = _normalize_json_schema(item)
        return normalized
    if isinstance(value, list):
        return [_normalize_json_schema(item) for item in value]
    return value


def _render_catalog(
    snapshot: _Snapshot,
    allowed: Optional[dict[str, bool]],
    mode: InvocationMode,
) -> str:
    rows: list[tuple[str, str, list[str]]] = []
    for namespace in snapshot.namespace_order:
        names = sorted(name for name in snapshot.namespace_tools.get(namespace, ())
                       if allowed is None or allowed.get(name, False))
        if names:
            rows.append((
                namespace,
                snapshot.namespace_descriptions.get(namespace, ""),
                names,
            ))
    if not rows:
        return ""
    if len(rows) == 1 and rows[0][0] == _DEFAULT_NAMESPACE:
        return ("<available-deferred-tools>\n" + "\n".join(rows[0][2]) + "\n</available-deferred-tools>")

    invocation = ("invoke it with call_tool" if mode == InvocationMode.DISPATCH else "call it directly")
    lines = [
        "<toolbox-catalog>",
        ("Deferred tools are not callable until loaded with tool_search; "
         f"after loading, {invocation}."),
    ]
    for namespace, description, names in rows:
        label = namespace or "(no namespace)"
        domain = f" ({description})" if description else ""
        exact_names = ", ".join(f'"{name}"' for name in names)
        lines.append(f"- namespace={label}{domain}; exact tool_names=[{exact_names}]")
    lines.append("Namespace filters the search scope and must not be prepended to a tool name.")
    lines.append("</toolbox-catalog>")
    return "\n".join(lines)


def _replace_catalog_placeholder(
    request: LlmRequest,
    catalog: str,
) -> None:
    if request.config is None:
        request.append_instructions([catalog] if catalog else [])
        return
    current = request.config.system_instruction
    if current is not None and TOOL_SEARCH_PLACEHOLDER in str(current):
        request.config.system_instruction = str(current).replace(
            TOOL_SEARCH_PLACEHOLDER,
            catalog,
            1,
        )
    elif catalog:
        request.append_instructions([catalog])


def _deduplicate_tools(tools: Sequence[BaseTool]) -> list[BaseTool]:
    result: list[BaseTool] = []
    seen: set[str] = set()
    for tool in tools:
        if tool.name in seen:
            continue
        seen.add(tool.name)
        result.append(tool)
    return result
