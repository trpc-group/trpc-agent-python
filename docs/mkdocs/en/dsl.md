# DSL

DSL code generator is used to convert business workflow JSON descriptions into runnable tRPC-Agent-Python projects.  
The generated code is based on trpc_agent_dsl.graph (see [graph.md](./graph.md)), suitable for a collaborative workflow of "DSL declaration + auto generation + secondary development".

## Command-Line Usage

Entry point:

```bash
python -m trpc_agent_dsl.codegen workflow.json [options]
```

Common parameters:

- workflow.json: Path to the DSL JSON file
- --dsl-text: Pass DSL JSON text directly (used when workflow.json is not provided)
- -o, --output-dir: Output directory (default `<workflow_stem>`)
- --overwrite: Allow writing to a non-empty directory

Examples:

```bash
# Output to the <workflow_stem> directory under the same path
python -m trpc_agent_dsl.codegen workflow.json

# Specify an output directory and overwrite
python -m trpc_agent_dsl.codegen workflow.json -o examples/dsl/my_graph --overwrite

# Pass DSL JSON text directly without providing a file
python -m trpc_agent_dsl.codegen --dsl-text "$(cat workflow.json)" -o examples/dsl/my_graph --overwrite
```

## Generated Project Structure

The basic structure is as follows:

```bash
.
├── README.md
├── .env
├── requirements.txt
├── run_agent.py
├── workflow.json
└── agent
    ├── __init__.py
    ├── agent.py
    ├── callbacks.py
    ├── config.py
    ├── nodes.py
    ├── prompts.py
    ├── state.py
    └── tools.py
```

File responsibilities:

- workflow.json: A copy of the input DSL
- run_agent.py: Local interactive entry point
- agent/agent.py: Graph assembly (`add_node` / `add_agent_node` / `add_edge` / `add_conditional_edges`)
- agent/nodes.py: Node functions, routing functions, and input_mapper mapping functions
- agent/state.py: WorkflowState and structured output models (if any)
- agent/config.py: Model and connection parameter construction (e.g., `env:OPENAI_API_KEY` resolution)
- agent/tools.py: MCP / memory_search / knowledge_search tool construction
- agent/prompts.py: Instruction constants
- agent/callbacks.py: Callback extension points (empty implementation by default)

## DSL Structure Overview

DSL definition: [trpc_agent_sdk/dsl/codegen/dsl_schema.json](../../../trpc_agent_sdk/dsl/codegen/dsl_schema.json).

## Currently Supported node_type (Python code generator)

Although the schema defines more types, the node types that the current Python code generator can stably generate are:

- builtin.start
- builtin.llmagent
- builtin.end
- builtin.transform
- builtin.code
- builtin.mcp
- builtin.knowledge_search
- builtin.set_state
- builtin.user_approval
- custom.* (generates placeholder functions with TODO)

Corresponding Graph APIs:

- builtin.llmagent -> graph.add_agent_node(...)
- builtin.mcp -> graph.add_mcp_node(...)
- builtin.knowledge_search -> graph.add_knowledge_node(...)
- builtin.code -> graph.add_code_node(...)
- Other nodes -> graph.add_node(...)

## Common Configurations for builtin.llmagent

For builtin.llmagent.config, the following fields should be prioritized:

- model_spec: Model configuration (provider / model_name / api_key / base_url / headers)
- instruction: System prompt, supports {{...}} template references
- temperature / max_tokens / top_p: Generation parameters
- output_format: text or json; when json, a schema is required
- tools: Unified tool definitions (currently mainly supports mcp / knowledge_search / memory_search)
- mcp_tools: Legacy MCP syntax for backward compatibility
- user_message: Overrides the user message sent to the model for this node, supports templates
- skills / executor: Agent Skills related configuration

When output_format.type = "json", the generator produces the corresponding Pydantic model in agent/state.py and mounts it via LlmAgent(..., output_schema=...).

## Expressions and Template Syntax

### 1. expr (transform / conditional / end / set_state)

conditional_edges[*].condition.cases[*].predicate.expression and builtin.transform.config.expr use a CEL-style subset, supporting:

- References: state.*, input.output_parsed.*, input.output_text, nodes.<id>.output_parsed.*, nodes.<id>.output_text
- Operators: &&, ||, ternary a ? b : c
- Functions: size(), string(), int(), double(), has()
- String contains: xxx.contains("...")

Limitations:

- Expressions using input.* require the current node to have "exactly one upstream node"
- builtin.end.config.expr is a more restricted subset (only supports state.* and nodes.* references)
- builtin.set_state.config.assignments[*].expr is also a restricted subset (does not support ternary/contains/has/size, etc.)

### 2. Template Strings {{...}}

The following fields support template references:

- builtin.llmagent.config.instruction
- builtin.llmagent.config.user_message
- builtin.knowledge_search.config.query

Template references can also use state.* / input.* / nodes.*.  
The generator compiles them into Python value-fetching logic; for user_message, it automatically generates an input_mapper and writes the mapped value into the child state's `STATE_KEY_USER_INPUT`.

## DSL to Generated Code Examples

The following examples illustrate "how DSL maps to code" using existing samples.

### Example 1: Conditional Routing (Classification-Based Dispatch)

DSL (excerpt, from [examples/dsl/classifier_mcp/workflow.json](../../../examples/dsl/classifier_mcp/workflow.json)):

```json
{
  "from": "classifier",
  "condition": {
    "cases": [
      {
        "predicate": {
          "expression": "input.output_parsed.classification == \"math_simple\"",
          "format": "cel"
        },
        "target": "simple_math_agent"
      }
    ]
  }
}
```

Generated code (excerpt, from examples/dsl/classifier_mcp/agent/nodes.py and agent/agent.py):

```python
def route_by_classification(state: WorkflowState) -> str:
    if state[STATE_KEY_NODE_RESPONSES]['classifier']['classification'] == "math_simple":
        return 'simple_math_agent'
    raise ValueError('No conditional case matched for route from classifier')

graph.add_conditional_edges(NODE_ID_CLASSIFIER, route_by_classification)
```

### Example 2: user_message Override and input_mapper

DSL (excerpt, from [examples/dsl/user_message_override/workflow.json](../../../examples/dsl/user_message_override/workflow.json)):

```json
{
  "id": "echo_agent",
  "node_type": "builtin.llmagent",
  "config": {
    "user_message": "{{input.output_parsed.overridden_user_message}}"
  }
}
```

Generated code (excerpt):

```python
def map_input_echo_agent(state: WorkflowState) -> dict[str, Any]:
    child_state = dict(state)
    child_state[STATE_KEY_USER_INPUT] = str(
        state[STATE_KEY_NODE_RESPONSES]['build_message']['overridden_user_message']
    )
    return child_state

graph.add_agent_node(
    NODE_ID_ECHO_AGENT,
    _create_echo_agent(),
    input_mapper=map_input_echo_agent,
    config=NodeConfig(name=NODE_ID_ECHO_AGENT, description='Echo agent'),
)
```

### Example 3: builtin.mcp Node Mapping

DSL (excerpt, from [examples/dsl/mcp_node/workflow.json](../../../examples/dsl/mcp_node/workflow.json)):

```json
{
  "id": "mcp_weather",
  "node_type": "builtin.mcp",
  "config": {
    "mcp": {
      "transport": "sse",
      "server_url": "http://.../mcp"
    },
    "function": "add"
  }
}
```

Generated code (excerpt):

```python
graph.add_mcp_node(
    NODE_ID_MCP_WEATHER,
    create_mcp_toolset_mcp_weather(),
    selected_tool_name='add',
    req_src_node='prepare_request',
    config=NodeConfig(name=NODE_ID_MCP_WEATHER, description='Call MCP add'),
)
```

## Common Notes

- `builtin.llmagent.config.model_spec.provider` currently only supports `openai`
- `knowledge_search.connector.type` currently only supports `trag`
- `builtin.code.config.executor_type` currently only supports `local`
- `builtin.user_approval` can only determine subsequent nodes via `config.routing`; explicit `edges` / `conditional_edges` cannot be configured for this node
- `state_variables` must not conflict with built-in state keys (e.g., `user_input`, `node_responses`, etc.)
- Some fields in the schema, even if writable, may not yet be mapped to generated code by the current code generator; it is recommended to refer to `examples/dsl/*/workflow.json` first

## Recommended Reading Order

1. Start with [examples/dsl/README.md](../../../examples/dsl/README.md)
2. Then look at a minimal example, such as [examples/dsl/memory_agent/workflow.json](../../../examples/dsl/memory_agent/workflow.json)
3. Review the branch routing example: [examples/dsl/classifier_mcp/workflow.json](../../../examples/dsl/classifier_mcp/workflow.json)
4. Review the RAG orchestration example: [examples/dsl/knowledge_search/workflow.json](../../../examples/dsl/knowledge_search/workflow.json)
5. Compare with `agent/*.py` in the same directory to see the final "DSL -> code" output
