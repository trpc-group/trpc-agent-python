# Tool Search

When an Agent has a large tool catalog, including every tool definition in each
model request increases input tokens, makes tool selection harder, and can
reduce prompt-cache hits. `ToolSearchToolSet` initially exposes only preset
tools and `tool_search`, then lets the model discover tools on demand.

## Core concepts

- **Tool**: A function the model can call, such as `get_weather`. Use
  `FunctionTool(get_weather)` to wrap a regular Python function.
- **ToolSearchToolSet**: A ToolSet that manages the tool catalog, performs
  searches, and controls when tools become available to the model. Add it to
  `LlmAgent.tools` to enable Tool Search.
- **Preset Tool**: Configured through `preset_tools`. Its complete schema is
  always sent directly to the model, so no search is required. This is useful
  for a small number of frequently used tools, such as a calculator or current
  time tool. `preset_tools` is optional; an always-visible tool may also be
  added directly to `LlmAgent.tools`.
- **Deferred Tool**: Configured through `deferred_tools`. It is initially
  searchable only through the catalog, and its complete schema is not sent to
  the model immediately. "Deferred" means deferred exposure to the model, not
  delayed execution or a background task.
- **Toolbox**: A named group of static Deferred Tools configured through
  `toolboxes`. Toolboxes organize tools by business domain, such as `weather`
  or `filesystem`; their tools remain deferred.
- **Namespace**: The Toolbox name used to limit the search scope and reduce
  ambiguity between similar tools in different domains. It is not part of a
  static tool's callable name.
- **Catalog**: A lightweight index sent to the model. It primarily contains
  namespaces, tool names, and descriptions, rather than complete tool schemas.
- **Discovery/loading**: After `tool_search` finds a Deferred Tool, the
  framework records it as discovered. Native mode sends its complete schema in
  the next model request; Dispatch mode returns the schema in the search result
  and permits `call_tool` to invoke it.
- **Session State**: Session-persistent state containing discovered tool names,
  not Tool objects or schemas. It lets later turns on the same Agent branch
  reuse tools discovered earlier.

Both `deferred_tools` and `toolboxes` configure static deferred tools. The only
difference is whether the tools are grouped:

```python
tool_search = ToolSearchToolSet(
    # Ungrouped tools use the default empty namespace.
    deferred_tools=[
        FunctionTool(search_document),
    ],
    # Grouped by domain; search with namespace="weather".
    toolboxes=[
        ToolSearchToolbox(
            name="weather",
            description="weather observations and forecasts",
            tools=[
                FunctionTool(get_weather),
                FunctionTool(get_forecast),
            ],
        ),
    ],
)
```

Use `deferred_tools` for a small catalog that does not need domain-scoped
search. Use `toolboxes` when the catalog is large, spans multiple domains, or
contains similar tools that benefit from namespace routing.

## Basic usage

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.tools import FunctionTool
from trpc_agent_sdk.tools import ToolSearchToolbox
from trpc_agent_sdk.tools import ToolSearchToolSet
from trpc_agent_sdk.tools.tool_search import TOOL_SEARCH_PLACEHOLDER

tool_search = ToolSearchToolSet(
    preset_tools=[FunctionTool(calculator)],
    toolboxes=[
        ToolSearchToolbox(
            name="weather",
            description="weather observations and forecasts",
            tools=[
                FunctionTool(get_weather),
                FunctionTool(get_forecast),
            ],
        ),
    ],
)

agent = LlmAgent(
    name="assistant",
    model=model,
    instruction=(
        "Use tool_search before calling a deferred tool.\n\n"
        f"{TOOL_SEARCH_PLACEHOLDER}"
    ),
    tools=[tool_search],
)
```

`preset_tools` are always sent directly to the model. Tools in
`deferred_tools` and `toolboxes` become callable only after `tool_search`
discovers them. Discovered names are stored in Session State and remain
available to later turns on the same Agent branch.

## Invocation modes

`ToolSearchOptions.invocation_mode` supports two modes:

- `InvocationMode.NATIVE` (default) adds discovered tool definitions to the
  next model request. The model invokes their original names.
- `InvocationMode.DISPATCH` invokes every discovered tool through
  `call_tool(tool_name, params)`. This keeps the request's tool definitions
  stable for large catalogs and prompt-cache-sensitive workloads.

Dispatch mode rejects calls to tools that were not loaded through search.
Permission filtering applies consistently to the catalog, search results, and
tool execution. In Native mode, a direct call to an unloaded tool receives
specific guidance to load it with `tool_search` and retry; the declaration-free
guard itself is never advertised to the model. The same guidance is returned
for a stale MCP tool name that has disappeared from the latest listing.

## Search and catalog injection

The dependency-free default search matches names, descriptions, and namespaces.
Prefix a keyword with `+` to require it, for example
`["+weather", "forecast"]`.

The model may pass the following arguments to `tool_search`:

- `queries`: A list of keywords or natural-language queries, such as
  `["weather forecast"]`.
- `tool_names`: Exact tool names to load. Names usually come from the catalog
  or `additional_candidates` returned by an earlier search.
- `namespace`: An optional Toolbox namespace that limits the search to one
  business domain.
- `max_results`: The maximum matches loaded by this request, capped at 10.
  Remaining matches are returned by name in `additional_candidates` so the
  model can load one precisely later.

Use `queries` when the exact tool name is unknown, and `tool_names` when it is
already known. For example:

```json
{
  "queries": ["future weather forecast"],
  "namespace": "weather",
  "max_results": 3
}
```

The namespace only limits the search scope; it is not part of a static tool's
name. Copy the exact name shown in the catalog into `tool_names`: use
`"get_forecast"`, not `"weather.get_forecast"`. The latter form is accepted as
a compatibility fallback only when its namespace and tool name both match.

By default, the catalog replaces `{deferred_tools_section}` in the system
instruction. If the placeholder is absent, the catalog is appended. Set
`catalog_in_description=True` to place it in the `tool_search` description.

For semantic search, pass `SemanticToolIndex(embedder)` to
`ToolSearchToolSet`. The embedder may implement asynchronous `aembed_query` /
`aembed_documents`, or synchronous `embed_query` / `embed_documents`. With
`embedding_fail_open=True`, embedding failures fall back to keyword search.

`SemanticToolIndex(embedder, vector_store=...)` accepts a custom
`ToolSearchVectorStore`; the default is `InMemoryToolSearchVectorStore`. A
custom store implements `upsert`, `delete`, and `search`, using either
synchronous or asynchronous methods.

Embedders may continue returning vectors directly. To report token usage, they
may instead return `(vectors, usage)`, where usage contains
`prompt_tokens` / `total_tokens` or `prompt_token_count` /
`total_token_count`. Each model request starts a new usage period. Read the
current turn with `tool_search.get_embedding_usage(ctx)` and pass `reset=True`
to clear it early.

## Main options

- `max_results`: Default number of search results.
- `permission_filter`: Returns whether each deferred tool is visible and
  callable for the current `InvocationContext`. Use it for tenant-, user-, or
  Agent-level authorization.
- `catalog_in_description`: Places the Catalog in the `tool_search`
  description. By default, it is placed in the system instruction.
- `invocation_mode`: Selects Native or Dispatch invocation.
- `embedding_fail_open`: Falls back to keyword search when semantic retrieval
  fails.
- `state_key_prefix`: Prefix for discovered-tool entries in Session State,
  preventing collisions with other framework state.
- `strict_validation`: Enables strict checks for duplicate namespaces,
  duplicate tools, and invalid configuration.

## Configuration tolerance

`ToolSearchOptions(strict_validation=True)` is the default: empty namespaces,
duplicate namespaces, and duplicate tools fail during initialization. For
large catalogs assembled dynamically, enable Go-style recovery with:

```python
ToolSearchOptions(strict_validation=False)
```

Tolerant mode logs warnings, merges duplicate static namespaces, keeps a
tool's first namespace owner, and skips empty toolboxes, invalid tools, or
colliding MCP namespaces. Reserved-name conflicts, different declarations for
the same tool name, and an invalid `max_results` still fail because they cannot
be recovered safely.

## Dynamic MCP tools

Wrap a dynamic ToolSet with `ToolSearchMCPToolbox`:

```python
from trpc_agent_sdk.tools import ToolSearchMCPToolbox

tool_search = ToolSearchToolSet(
    mcp_toolboxes=[
        ToolSearchMCPToolbox(
            server_name="github",
            description="GitHub MCP tools",
            toolset=mcp_toolset,
        ),
    ],
)
```

`ToolSearchMCPToolbox` differs from a static `ToolSearchToolbox`: a static
Toolbox owns a fixed list of tools, while an MCP Toolbox owns a dynamic ToolSet
that can retrieve the latest declarations from an MCP service whenever the
framework builds a tool snapshot.

Dynamic names use `mcp__github__<tool_name>` to avoid collisions across
servers. Closing `ToolSearchToolSet` also closes its dynamic ToolSets. The
framework compares declaration fingerprints: changed or removed tools have
their stale semantic vectors cleared, while unchanged tools keep their existing
index entries. Concurrent refreshes cannot publish vectors from stale
declarations.

A **declaration fingerprint** is derived from a tool's name, description, and
parameter schema. It detects changes to an MCP tool definition; it is not an
authentication credential and is never sent to the model.

See the runnable
[`examples/tool_search`](https://github.com/trpc-group/trpc-agent-python/tree/main/examples/tool_search).
