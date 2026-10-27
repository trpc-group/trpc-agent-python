# Hy-Memory Service Example

This example integrates the
[Hy-Memory](https://github.com/Tencent-Hunyuan/Hy-Memory) Python SDK with
`trpc-agent` through `HyMemoryService`.

It writes a preference in `write-session`, then uses the `load_memory` tool to
recall it from a separate `recall-session`.

## Install

From the repository root:

```bash
./build.sh "[hy-memory]"
source .venv/bin/activate
```

Hy-Memory stores data locally by default. Its core package includes ChromaDB
and Kuzu. Alternative backends can be installed with the corresponding
Hy-Memory extras.

## Configure

```bash
cd examples/memory_service_with_hy_memory
cp .env.example .env
```

Fill in:

- `TRPC_AGENT_*`: model used by the example Agent.
- `MEMORY_LLM_*`: model used by Hy-Memory to extract and reconcile memories.
  The example reuses `TRPC_AGENT_*`; replace the values if Memory needs a
  different model.
- `EMBEDDING_SERVICE_ENDPOINT`: custom batch embedding endpoint.
- `EMBEDDING_SERVICE_X_CLIENT_SSID` and
  `EMBEDDING_SERVICE_X_HOST_KEY_ACCESS`: Cookie credentials for that endpoint.
- `EMBEDDING_SERVICE_DIMS`: must match the embedding model output dimension.
- `EMBEDDING_SERVICE_MAX_RETRIES`, `EMBEDDING_SERVICE_RETRY_DELAY`, and
  `EMBEDDING_SERVICE_TIMEOUT`: request retry and timeout controls.
- `HY_MEMORY_*`: example and adapter routing or isolation settings; these are
  not native Hy-Memory SDK environment variables.

`custom_embedder.py` implements Hy-Memory's public `EmbeddingService` protocol.
It sends `{"inputs": [...]}`, attaches the required Cookie, parses
`data.embedding`, and validates result count and 1024-dimensional vectors.
The `EMBEDDING_SERVICE_*` variables are read by this example adapter, not by
the Hy-Memory SDK. The SDK's native `MEMORY_EMBEDDER_*` settings are not needed
when this custom embedder is supplied.

The LLM API key can be shared when the Agent and Hy-Memory use the same
OpenAI-compatible endpoint and account:

```bash
MEMORY_LLM_API_KEY=${TRPC_AGENT_API_KEY}
MEMORY_LLM_BASE_URL=${TRPC_AGENT_BASE_URL}
MEMORY_LLM_MODEL=${TRPC_AGENT_MODEL_NAME}
```

`MEMORY_LLM_API_KEY` is loaded into Hy-Memory's `LLMConfig` and passed to its
OpenAI-compatible client. It authenticates memory extraction, summarization,
conflict reconciliation, and agentic query routing. It is not read directly
by `HyMemoryService`.

See the
[Hy-Memory usage guide](https://github.com/Tencent-Hunyuan/Hy-Memory/blob/main/docs/usage.md)
for all storage and model options.

## Run

```bash
python run_agent.py
```

By default, the script generates a new user ID on each process run so the
write step always validates fresh extraction instead of reusing an existing
memory. Set `HY_MEMORY_DEMO_USER_ID=demo-user` to reuse one user and verify
persistence across separate process runs.

Expected flow:

```text
write-session:
  "Remember that I prefer concise answers and use Python..."
  -> Runner calls HyMemoryService.store_session()
  -> HyMemoryClient.async_add(scene="chat")

recall-session:
  "What response style and backend language do I prefer?"
  -> Agent calls load_memory
  -> HyMemoryService.search_memory()
  -> HyMemoryClient.async_search() returns the cross-session memory
```

## Why Use This Integration?

The verified result demonstrates several practical advantages over storing and
replaying raw conversation history:

- **Automatic memory creation**: the Runner persists each completed turn, so
  the Agent does not need a separate save-memory tool or prompt-driven save
  decision.
- **Compact semantic memory**: Hy-Memory extracted the durable fact
  `The user prefers concise answers and uses Python for backend development`
  instead of injecting the complete previous conversation.
- **Cross-session continuity**: a second, independent session recalled the
  preference and answered correctly without receiving the first session's
  history.
- **Fast recall**: the measured semantic search completed in about 98 ms.
  Memory extraction stays on the post-turn write path rather than adding that
  latency to the next answer.
- **Existing infrastructure reuse**: the custom adapter connects directly to
  the existing authenticated BGE-M3 service and validates every returned
  vector against the configured 1024 dimensions.
- **Local data control**: the verified setup stores vectors in local Chroma
  while allowing teams to use their own LLM and embedding endpoints.
- **Noise and duplication control**: reasoning parts are excluded before
  persistence, and Hy-Memory extraction/reconciliation avoids creating a new
  long-term fact when a later turn contains no new information.
- **Room to grow**: the same integration supports Chat, Working, and Coding
  memory without changing the framework's Runner or `load_memory` interface.

Verified answer from a fresh user and a different session:

```text
Based on my memory, you prefer:
- Response style: Concise answers
- Backend language: Python for backend development
```

Observed timing in this environment:

- Fresh extraction and persistence: about 5.9 seconds.
- Remote embedding/L1 write: about 4.2 seconds.
- Memory extraction: about 1.4 seconds.
- Cross-session recall: about 98 milliseconds.

These numbers are environment-specific, but they confirm the complete path
from framework Event, through the remote BGE-M3 service and Hy-Memory
extraction, to correct cross-session recall.

## Working and Coding Memory

Set `HY_MEMORY_SCENE` to `working` or `coding` and configure a stable
`HY_MEMORY_WORKSPACE_ID`. For coding memory, `HY_MEMORY_BRANCH` can further
scope recall:

```bash
HY_MEMORY_SCENE=coding
HY_MEMORY_WORKSPACE_ID=github.com/example/service
HY_MEMORY_BRANCH=main
```

Working and coding extraction requires tool traces. `HyMemoryService`
preserves framework `FunctionCall` and `FunctionResponse` events as OpenAI
`tool_calls` and `role="tool"` messages. If a turn contains no tool trace,
Hy-Memory falls back to its chat pipeline.

For request-specific routing, set options on the `AgentContext`:

```python
from trpc_agent_sdk.memory import set_hy_memory_options

agent_context = AgentContext()
set_hy_memory_options(
    agent_context,
    {
        "scene": "coding",
        "workspace_id": "github.com/example/service",
        "branch": "fix/timeout",
    },
)
```
