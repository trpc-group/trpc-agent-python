# Tool Search

This example keeps domain tools out of the initial model request and lets the
model discover only the tools needed for the current conversation. It contains:

- `calculator`, a preset tool that is always available;
- deferred `weather` and `billing` toolboxes;
- Native and Dispatch invocation modes;
- session-scoped persistence of discovered tools.

## Run

```bash
cd examples/tool_search
cp .env.example .env
# Edit model settings in .env.
uv run python run_agent.py
```

For the default request, the model first calls `tool_search` to load
`get_forecast`, then invokes that tool. In `native` mode it calls
`get_forecast` directly. In `dispatch` mode it calls `call_tool` with the
discovered tool name and arguments.

Change `TOOL_SEARCH_QUERY` to test another namespace, for example:

```dotenv
TOOL_SEARCH_QUERY=Create a 99.5 dollar invoice for Alice.
```

The tool catalog is injected at `{deferred_tools_section}` in the Agent
instruction. If the placeholder is absent, it is appended to the system
instruction. Set `TOOL_SEARCH_CATALOG_IN_DESCRIPTION=true` to put the catalog
in the `tool_search` description instead.
