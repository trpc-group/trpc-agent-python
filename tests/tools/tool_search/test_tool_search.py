# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for deferred tool discovery and loading."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from trpc_agent_sdk.agents._base_agent import BaseAgent
from trpc_agent_sdk.agents.core._tools_processor import ToolsProcessor
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.context import create_agent_context
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.tools import BaseToolSet
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.tools import LongRunningFunctionTool
from trpc_agent_sdk.tools import StreamingProgressTool
from trpc_agent_sdk.tools.tool_search import CALL_TOOL_NAME
from trpc_agent_sdk.tools.tool_search import InMemoryToolSearchVectorStore
from trpc_agent_sdk.tools.tool_search import InvocationMode
from trpc_agent_sdk.tools.tool_search import MCPToolbox
from trpc_agent_sdk.tools.tool_search import SemanticToolIndex
from trpc_agent_sdk.tools.tool_search import TOOL_SEARCH_PLACEHOLDER
from trpc_agent_sdk.tools.tool_search import TOOL_SEARCH_TOOL_NAME
from trpc_agent_sdk.tools.tool_search import Toolbox
from trpc_agent_sdk.tools.tool_search import ToolSearchDocument
from trpc_agent_sdk.tools.tool_search import ToolSearchOptions
from trpc_agent_sdk.tools.tool_search import ToolSearchToolSet
from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import GenerateContentConfig


class _StubAgent(BaseAgent):

    async def _run_async_impl(self, ctx):
        yield


class _DynamicToolSet(BaseToolSet):

    def __init__(self, tools):
        super().__init__(name="dynamic")
        self.tools = tools
        self.closed = False
        self.raise_error = False

    async def get_tools(self, invocation_context=None):
        if self.raise_error:
            raise RuntimeError("dynamic listing unavailable")
        return list(self.tools)

    async def close(self):
        self.closed = True


def _tool(name: str, description: str):

    async def implementation(value: str = ""):
        return {
            "tool": name,
            "value": value,
        }

    implementation.__name__ = name
    implementation.__doc__ = description
    return FunctionTool(implementation)


@pytest.fixture
def context():
    service = InMemorySessionService()
    session = asyncio.run(service.create_session(
        app_name="test",
        user_id="user",
        session_id="session",
    ))
    agent = _StubAgent(name="search_agent")
    return InvocationContext(
        session_service=service,
        invocation_id="invocation",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
    )


async def _search(toolset, context, **args):
    tools = await toolset.get_tools(context)
    search = next(tool for tool in tools if tool.name == TOOL_SEARCH_TOOL_NAME)
    return await search.run_async(
        tool_context=context,
        args=args,
    )


class TestKeywordSearch:

    async def test_loads_deferred_tool_and_exposes_it_next_request(
        self,
        context,
    ):
        read = _tool("read_file", "Read a file from disk")
        write = _tool("write_file", "Write text to a file")
        toolset = ToolSearchToolSet(
            toolboxes=[Toolbox(
                name="filesystem",
                description="file and directory operations",
                tools=[read, write],
            )],
            options=ToolSearchOptions(max_results=1),
        )

        initial = await toolset.get_tools(context)
        assert [tool.name for tool in initial] == [TOOL_SEARCH_TOOL_NAME]

        result = await _search(
            toolset,
            context,
            namespace="filesystem",
            queries=["write file"],
        )

        assert result["tools"][0]["name"] == "write_file"
        loaded = await toolset.get_tools(context)
        assert [tool.name for tool in loaded] == [
            TOOL_SEARCH_TOOL_NAME,
            "write_file",
        ]

    async def test_exact_names_are_case_insensitive_and_cross_namespace(
        self,
        context,
    ):
        toolset = ToolSearchToolSet(toolboxes=[
            Toolbox("filesystem", "files", [_tool("read_file", "Read file")]),
            Toolbox("network", "network", [_tool("http_get", "HTTP GET")]),
        ])

        result = await _search(
            toolset,
            context,
            tool_names=[
                "HTTP_GET",
                "filesystem.read_file",
                "http_get",
            ],
        )

        assert [item["name"] for item in result["tools"]] == [
            "http_get",
            "read_file",
        ]

    async def test_search_status_distinguishes_partially_and_fully_loaded(
        self,
        context,
    ):
        toolset = ToolSearchToolSet(deferred_tools=[
            _tool("read_file", "Read file"),
            _tool("write_file", "Write file"),
        ])
        await _search(
            toolset,
            context,
            tool_names=["read_file"],
        )

        partial = await _search(
            toolset,
            context,
            tool_names=["read_file", "write_file"],
        )
        assert "already_loaded=true" in partial["status"]

        complete = await _search(
            toolset,
            context,
            tool_names=["read_file", "write_file"],
        )
        assert complete["status"].startswith("All matches were loaded earlier", )

    async def test_qualified_alias_requires_matching_namespace(
        self,
        context,
    ):
        toolset = ToolSearchToolSet(toolboxes=[
            Toolbox("filesystem", "files", [_tool("read_file", "Read file")]),
        ])

        result = await _search(
            toolset,
            context,
            tool_names=["network.read_file"],
        )

        assert result["tools"] == []

    async def test_required_terms_and_overflow(self, context):
        toolset = ToolSearchToolSet(
            deferred_tools=[
                _tool("read_file", "Read local file content"),
                _tool("read_url", "Read remote URL content"),
                _tool("write_file", "Write local file content"),
            ],
            options=ToolSearchOptions(max_results=1),
        )

        result = await _search(
            toolset,
            context,
            queries=["+read content"],
        )

        assert len(result["tools"]) == 1
        assert result["additional_candidates"] == ["read_url"]

    async def test_unknown_namespace_returns_catalog(self, context):
        toolset = ToolSearchToolSet(toolboxes=[Toolbox("filesystem", "files", [_tool("read_file", "Read file")])])

        result = await _search(
            toolset,
            context,
            namespace="missing",
            queries=["read"],
        )

        assert result["status"] == "unknown_namespace"
        assert result["catalog"] == [{
            "name": "filesystem",
            "description": "files",
        }]

    async def test_namespace_without_query_lists_tools(self, context):
        toolset = ToolSearchToolSet(toolboxes=[
            Toolbox(
                "filesystem",
                "files",
                [
                    _tool("write_file", "Write file"),
                    _tool("read_file", "Read file"),
                ],
            )
        ])

        result = await _search(
            toolset,
            context,
            namespace="filesystem",
        )

        assert [item["name"] for item in result["tools"]] == [
            "read_file",
            "write_file",
        ]


class TestCatalogAndPermissions:

    async def test_tools_processor_refreshes_native_tools_after_search(
        self,
        context,
    ):
        toolset = ToolSearchToolSet(deferred_tools=[_tool("read_file", "Read file")], )
        processor = ToolsProcessor([toolset])
        first_request = LlmRequest(config=GenerateContentConfig(system_instruction="Use tools."), )

        await processor.process_llm_request(context, first_request)
        assert set(first_request.tools_dict) == {TOOL_SEARCH_TOOL_NAME}

        await _search(toolset, context, tool_names=["read_file"])
        second_request = LlmRequest(config=GenerateContentConfig(system_instruction="Use tools."), )
        await processor.process_llm_request(context, second_request)

        assert set(second_request.tools_dict) == {
            TOOL_SEARCH_TOOL_NAME,
            "read_file",
        }
        assert "<available-deferred-tools>" in str(second_request.config.system_instruction)

    async def test_catalog_replaces_placeholder(self, context):
        toolset = ToolSearchToolSet(
            toolboxes=[Toolbox("filesystem", "file operations", [_tool("read_file", "Read file")])])
        search = (await toolset.get_tools(context))[0]
        request = LlmRequest(config=GenerateContentConfig(
            system_instruction=(f"Use tools.\n{TOOL_SEARCH_PLACEHOLDER}"), ), )

        await search.process_request(
            tool_context=context,
            llm_request=request,
        )

        instruction = str(request.config.system_instruction)
        assert TOOL_SEARCH_PLACEHOLDER not in instruction
        assert "<toolbox-catalog>" in instruction
        assert "read_file" in instruction
        assert 'exact tool_names=["read_file"]' in instruction
        assert "must not be prepended" in instruction

    async def test_permission_hides_searches_and_loaded_tools(self, context):

        def permissions(_ctx, names):
            return {name: name != "delete_file" for name in names}

        toolset = ToolSearchToolSet(
            deferred_tools=[
                _tool("read_file", "Read file"),
                _tool("delete_file", "Delete file"),
            ],
            options=ToolSearchOptions(permission_filter=permissions),
        )

        result = await _search(
            toolset,
            context,
            tool_names=["read_file", "delete_file"],
        )

        assert [item["name"] for item in result["tools"]] == ["read_file"]
        assert "delete_file" not in [tool.name for tool in await toolset.get_tools(context)]

    async def test_preset_is_visible_but_not_keyword_searchable(self, context):
        preset = _tool("calculator", "Calculate numbers")
        toolset = ToolSearchToolSet(
            preset_tools=[preset],
            deferred_tools=[_tool("weather", "Get weather")],
        )

        assert [tool.name for tool in await toolset.get_tools(context)] == [
            "calculator",
            TOOL_SEARCH_TOOL_NAME,
        ]
        result = await _search(
            toolset,
            context,
            queries=["calculate"],
        )
        assert result["tools"] == []


class TestConfigurationTolerance:

    def test_strict_mode_rejects_duplicate_namespace(self):
        with pytest.raises(ValueError, match="Duplicate toolbox namespace"):
            ToolSearchToolSet(toolboxes=[
                Toolbox("filesystem", "files", []),
                Toolbox("filesystem", "more files", []),
            ])

    async def test_tolerant_mode_merges_and_keeps_first_tool_owner(
        self,
        context,
    ):
        read = _tool("read_file", "Read file")
        write = _tool("write_file", "Write file")
        with patch("trpc_agent_sdk.tools.tool_search._toolset.logger.warning", ) as warning:
            toolset = ToolSearchToolSet(
                toolboxes=[
                    Toolbox("filesystem", "", [read]),
                    Toolbox("filesystem", "file operations", [write]),
                    Toolbox("document", "document operations", [read]),
                    Toolbox("", "invalid", [_tool("ignored", "Ignored")]),
                ],
                options=ToolSearchOptions(strict_validation=False),
            )

        filesystem = await _search(
            toolset,
            context,
            namespace="filesystem",
        )
        assert [item["name"] for item in filesystem["tools"]] == [
            "read_file",
            "write_file",
        ]

        document = await _search(
            toolset,
            context,
            namespace="document",
        )
        assert document["tools"] == []
        warning.assert_any_call(
            "Merging duplicate toolbox namespace %r",
            "filesystem",
        )
        warning.assert_any_call(
            "Tool %r is already registered in namespace %r; keeping "
            "the first owner and ignoring namespace %r",
            "read_file",
            "filesystem",
            "document",
        )
        warning.assert_any_call(
            "Skipping toolbox with empty name: description=%r",
            "invalid",
        )

    def test_tolerant_mode_rejects_conflicting_declarations(self):
        with pytest.raises(ValueError, match="Conflicting declarations"):
            ToolSearchToolSet(
                toolboxes=[
                    Toolbox(
                        "first",
                        "",
                        [_tool("shared", "First declaration")],
                    ),
                    Toolbox(
                        "second",
                        "",
                        [_tool("shared", "Second declaration")],
                    ),
                ],
                options=ToolSearchOptions(strict_validation=False),
            )

    async def test_tolerant_mode_skips_colliding_mcp_namespace(
        self,
        context,
    ):
        dynamic = _DynamicToolSet([_tool("read", "Read repository")])
        with patch("trpc_agent_sdk.tools.tool_search._toolset.logger.warning", ) as warning:
            toolset = ToolSearchToolSet(
                toolboxes=[Toolbox(
                    "github",
                    "static GitHub tools",
                    [_tool("search", "Search repository")],
                )],
                mcp_toolboxes=[MCPToolbox(
                    server_name="github",
                    description="dynamic GitHub tools",
                    toolset=dynamic,
                )],
                options=ToolSearchOptions(strict_validation=False),
            )

        result = await _search(
            toolset,
            context,
            namespace="github",
        )
        assert [item["name"] for item in result["tools"]] == ["search"]
        warning.assert_called_once_with(
            "Skipping MCP toolbox %r because its namespace is "
            "already registered",
            "github",
        )

    async def test_tolerant_mode_deduplicates_dynamic_listing(
        self,
        context,
    ):
        read = _tool("read", "Read repository")
        dynamic = _DynamicToolSet([read, read])
        toolset = ToolSearchToolSet(
            mcp_toolboxes=[MCPToolbox(
                server_name="github",
                description="GitHub tools",
                toolset=dynamic,
            )],
            options=ToolSearchOptions(strict_validation=False),
        )

        with patch("trpc_agent_sdk.tools.tool_search._toolset.logger.warning", ) as warning:
            result = await _search(
                toolset,
                context,
                namespace="github",
            )
        assert [item["name"] for item in result["tools"]] == [
            "mcp__github__read",
        ]
        warning.assert_called_once_with(
            "Dynamic tool %r is listed more than once by namespace %r; "
            "keeping the first entry",
            "mcp__github__read",
            "github",
        )


class TestInvocationModes:

    async def test_dispatch_keeps_fixed_surface_and_calls_loaded_tool(
        self,
        context,
    ):
        toolset = ToolSearchToolSet(
            deferred_tools=[_tool("echo", "Echo a value")],
            options=ToolSearchOptions(invocation_mode=InvocationMode.DISPATCH, ),
        )

        assert [tool.name for tool in await toolset.get_tools(context)] == [
            TOOL_SEARCH_TOOL_NAME,
            CALL_TOOL_NAME,
        ]
        result = await _search(
            toolset,
            context,
            tool_names=["echo"],
        )
        assert result["tools"][0]["input_schema"]["type"] == "object"

        tools = await toolset.get_tools(context)
        assert [tool.name for tool in tools] == [
            TOOL_SEARCH_TOOL_NAME,
            CALL_TOOL_NAME,
        ]
        call_tool = next(tool for tool in tools if tool.name == CALL_TOOL_NAME)
        called = await call_tool.run_async(
            tool_context=context,
            args={
                "tool_name": "echo",
                "params": {
                    "value": "hello"
                },
            },
        )
        assert called == {
            "tool": "echo",
            "value": "hello",
        }

    async def test_dispatch_rejects_unloaded_tool(self, context):
        toolset = ToolSearchToolSet(
            deferred_tools=[_tool("echo", "Echo a value")],
            options=ToolSearchOptions(invocation_mode=InvocationMode.DISPATCH, ),
        )
        call_tool = next(tool for tool in await toolset.get_tools(context) if tool.name == CALL_TOOL_NAME)

        result = await call_tool.run_async(
            tool_context=context,
            args={
                "tool_name": "echo",
                "params": {},
            },
        )

        assert "not loaded" in result["message"]

    async def test_dispatch_aggregates_progress_streaming_tool(
        self,
        context,
    ):

        async def progress(task: str):
            """Report progress for a task."""
            yield {"task": task, "status": "started"}
            yield {"task": task, "status": "done"}

        toolset = ToolSearchToolSet(
            deferred_tools=[StreamingProgressTool(progress)],
            options=ToolSearchOptions(invocation_mode=InvocationMode.DISPATCH, ),
        )
        await _search(toolset, context, tool_names=["progress"])
        call_tool = next(tool for tool in await toolset.get_tools(context) if tool.name == CALL_TOOL_NAME)

        result = await call_tool.run_async(
            tool_context=context,
            args={
                "tool_name": "progress",
                "params": {
                    "task": "test"
                },
            },
        )

        assert result == {
            "chunks": [
                {
                    "task": "test",
                    "status": "started",
                },
                {
                    "task": "test",
                    "status": "done",
                },
            ],
        }


class TestDynamicAndSemantic:

    async def test_mcp_toolbox_renames_and_retains_last_nonempty_snapshot(
        self,
        context,
    ):
        dynamic = _DynamicToolSet([_tool("read", "Read repository")])
        toolset = ToolSearchToolSet(mcp_toolboxes=[
            MCPToolbox(
                server_name="github",
                description="GitHub repository operations",
                toolset=dynamic,
            )
        ])

        first = await _search(
            toolset,
            context,
            namespace="github",
        )
        assert first["tools"][0]["name"] == "mcp__github__read"

        dynamic.tools = []
        names = [tool.name for tool in await toolset.get_tools(context)]
        assert "mcp__github__read" in names

        dynamic.raise_error = True
        names = [tool.name for tool in await toolset.get_tools(context)]
        assert "mcp__github__read" in names

        await toolset.close()
        assert dynamic.closed is True

    async def test_mcp_toolbox_preserves_long_running_marker(
        self,
        context,
    ):

        async def import_data(source: str):
            """Import data from a source."""
            return {"source": source}

        dynamic = _DynamicToolSet([LongRunningFunctionTool(import_data)], )
        toolset = ToolSearchToolSet(
            mcp_toolboxes=[MCPToolbox(
                server_name="warehouse",
                description="Warehouse operations",
                toolset=dynamic,
            )])

        result = await _search(
            toolset,
            context,
            namespace="warehouse",
        )
        assert result["tools"][0]["name"] == "mcp__warehouse__import_data"
        loaded = await toolset.get_tools(context)
        renamed = next(tool for tool in loaded if tool.name == "mcp__warehouse__import_data")
        assert isinstance(renamed, LongRunningFunctionTool)

    async def test_native_stale_mcp_call_returns_search_guidance(
        self,
        context,
    ):
        dynamic = _DynamicToolSet([_tool("read", "Read repository")])
        toolset = ToolSearchToolSet(mcp_toolboxes=[
            MCPToolbox(
                server_name="github",
                description="GitHub repository operations",
                toolset=dynamic,
            )
        ])
        await _search(
            toolset,
            context,
            tool_names=["mcp__github__read"],
        )

        dynamic.tools = [_tool("write", "Write repository")]
        processor = ToolsProcessor([toolset])
        events = [
            event async for event in processor.execute_tools_async(
                [FunctionCall(
                    id="call-1",
                    name="mcp__github__read",
                    args={},
                )],
                context,
            )
        ]

        assert len(events) == 1
        assert events[0].error_code is None
        response = events[0].content.parts[0].function_response.response
        assert "not loaded yet" in str(response)
        assert "tool_search" in str(response)

    async def test_semantic_search_ranks_by_similarity(self, context):

        class Embedder:

            async def aembed_documents(self, texts):
                return [[1.0, 0.0] if "weather" in text else [0.0, 1.0] for text in texts]

            async def aembed_query(self, text):
                return [1.0, 0.0] if "forecast" in text else [0.0, 1.0]

        toolset = ToolSearchToolSet(
            deferred_tools=[
                _tool("weather", "Get weather forecast"),
                _tool("database", "Run SQL query"),
            ],
            semantic_index=SemanticToolIndex(Embedder()),
        )

        result = await _search(
            toolset,
            context,
            queries=["tomorrow forecast"],
            max_results=1,
        )

        assert result["tools"][0]["name"] == "weather"

    async def test_semantic_failure_can_fall_back_to_keyword(
        self,
        context,
    ):

        class BrokenEmbedder:

            async def aembed_documents(self, texts):
                raise RuntimeError("embedding unavailable")

            async def aembed_query(self, text):
                raise RuntimeError("embedding unavailable")

        toolset = ToolSearchToolSet(
            deferred_tools=[_tool("weather", "Get weather forecast")],
            semantic_index=SemanticToolIndex(BrokenEmbedder()),
            options=ToolSearchOptions(embedding_fail_open=True),
        )

        result = await _search(
            toolset,
            context,
            queries=["weather"],
        )

        assert result["tools"][0]["name"] == "weather"

    async def test_mcp_forgets_changed_and_removed_semantic_entries(
        self,
        context,
    ):

        class Embedder:

            async def aembed_documents(self, texts):
                return [[1.0, 0.0] for _ in texts]

            async def aembed_query(self, text):
                return [1.0, 0.0]

        class RecordingSemanticIndex(SemanticToolIndex):

            def __init__(self):
                super().__init__(Embedder())
                self.forgotten = []

            async def forget(self, names):
                self.forgotten.extend(names)
                await super().forget(names)

        semantic_index = RecordingSemanticIndex()
        dynamic = _DynamicToolSet([
            _tool("read", "Read repository"),
            _tool("write", "Write repository"),
        ])
        toolset = ToolSearchToolSet(
            mcp_toolboxes=[
                MCPToolbox(
                    server_name="github",
                    description="GitHub repository operations",
                    toolset=dynamic,
                )
            ],
            semantic_index=semantic_index,
        )

        await _search(
            toolset,
            context,
            namespace="github",
            queries=["repository"],
        )
        assert semantic_index.forgotten == []

        dynamic.tools = [
            _tool("read", "Read repository content"),
            _tool("write", "Write repository"),
        ]
        await toolset.get_tools(context)
        assert semantic_index.forgotten == ["mcp__github__read"]

        dynamic.tools = [_tool("write", "Write repository")]
        await toolset.get_tools(context)
        assert semantic_index.forgotten == [
            "mcp__github__read",
            "mcp__github__read",
        ]

    async def test_semantic_index_does_not_publish_stale_embedding(self):
        embedding_started = asyncio.Event()
        release_embedding = asyncio.Event()

        class Embedder:

            async def aembed_documents(self, texts):
                embedding_started.set()
                await release_embedding.wait()
                return [[1.0, 0.0] for _ in texts]

            async def aembed_query(self, text):
                return [1.0, 0.0]

        document = ToolSearchDocument(
            name="read",
            description="Read repository",
            namespace="github",
            text="Tool: read\nDescription: Read repository",
            fingerprint="old",
        )
        current = True
        index = SemanticToolIndex(Embedder())
        search = asyncio.create_task(
            index.search(
                "repository",
                [document],
                1,
                verifier=lambda _name, _fingerprint: current,
            ))

        await embedding_started.wait()
        current = False
        release_embedding.set()

        assert await search == []

    async def test_custom_vector_store_and_embedding_usage(self, context):

        class RecordingStore(InMemoryToolSearchVectorStore):

            def __init__(self):
                super().__init__()
                self.upserted = []
                self.searches = 0

            async def upsert(self, name, vector):
                self.upserted.append(name)
                super().upsert(name, vector)

            async def search(self, query_vector, candidate_names, limit):
                self.searches += 1
                return super().search(
                    query_vector,
                    candidate_names,
                    limit,
                )

        class Embedder:

            async def aembed_documents(self, texts):
                return (
                    [[1.0, 0.0] for _ in texts],
                    {
                        "prompt_tokens": 4,
                        "total_tokens": 4,
                    },
                )

            async def aembed_query(self, text):
                return (
                    [1.0, 0.0],
                    {
                        "prompt_token_count": 2,
                        "total_token_count": 2,
                    },
                )

        store = RecordingStore()
        toolset = ToolSearchToolSet(
            deferred_tools=[_tool("weather", "Get weather")],
            semantic_index=SemanticToolIndex(
                Embedder(),
                vector_store=store,
            ),
        )
        processor = ToolsProcessor([toolset])
        await processor.process_llm_request(
            context,
            LlmRequest(config=GenerateContentConfig(system_instruction="Use tools.", )),
        )

        result = await _search(
            toolset,
            context,
            queries=["forecast"],
        )

        assert result["tools"][0]["name"] == "weather"
        assert store.upserted == ["weather"]
        assert store.searches == 1
        usage = toolset.get_embedding_usage(context)
        assert usage is not None
        assert usage.prompt_tokens == 6
        assert usage.total_tokens == 6
        await processor.process_llm_request(
            context,
            LlmRequest(config=GenerateContentConfig(system_instruction="Use tools.", )),
        )
        assert toolset.get_embedding_usage(context).total_tokens == 0

    async def test_native_unloaded_call_returns_search_guidance(
        self,
        context,
    ):
        toolset = ToolSearchToolSet(deferred_tools=[_tool("echo", "Echo a value")], )
        processor = ToolsProcessor([toolset])
        events = [
            event async for event in processor.execute_tools_async(
                [FunctionCall(
                    id="call-1",
                    name="echo",
                    args={"value": "hello"},
                )],
                context,
            )
        ]

        assert len(events) == 1
        assert events[0].error_code is None
        response = events[0].content.parts[0].function_response.response
        assert "not loaded yet" in str(response)
        assert "tool_search" in str(response)


class TestState:

    async def test_parallel_searches_merge_state(self, context):
        toolset = ToolSearchToolSet(deferred_tools=[
            _tool("read_file", "Read file"),
            _tool("write_file", "Write file"),
        ])

        await asyncio.gather(
            _search(toolset, context, tool_names=["read_file"]),
            _search(toolset, context, tool_names=["write_file"]),
        )

        assert [tool.name for tool in await toolset.get_tools(context)] == [
            TOOL_SEARCH_TOOL_NAME,
            "read_file",
            "write_file",
        ]
        discovered_keys = [
            key for key, value in context.event_actions.state_delta.items()
            if ":discovered_tools:" in key and value is True
        ]
        assert len(discovered_keys) == 2

    async def test_branch_state_isolated(self, context):
        toolset = ToolSearchToolSet(deferred_tools=[_tool("read_file", "Read file")], )
        context.branch = "branch-a"
        await _search(toolset, context, tool_names=["read_file"])
        assert "read_file" in [tool.name for tool in await toolset.get_tools(context)]

        context.branch = "branch-b"
        assert "read_file" not in [tool.name for tool in await toolset.get_tools(context)]
