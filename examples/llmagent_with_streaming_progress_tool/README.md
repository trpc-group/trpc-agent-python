# Streaming Progress Tool

This example shows how to expose a **long-running tool that streams progress
events to the user in real time**, using `StreamingProgressTool`.

The wrapped function is an `async def` generator (`async def fn(...): yield ...`).
Every `yield` is surfaced to the runner as a `partial=True` Event tagged with
`custom_metadata={"tool_progress": True, ...}`. The **last** yielded value is
*also* the final `function_response` returned to the LLM.

```text
yield progress_1       --> partial Event (live)
yield progress_2       --> partial Event (live)
yield progress_3       --> partial Event (live) AND final function_response
```

This is different from the other two streaming-ish tools shipped with the SDK:

| Class                       | What gets streamed                                  |
| --------------------------- | --------------------------------------------------- |
| `StreamingFunctionTool`     | The *arguments* the LLM is generating for the call. |
| `LongRunningFunctionTool`   | Nothing intermediate; just marks the call as slow.  |
| **`StreamingProgressTool`** | The tool's *own* execution progress.                |

## Run

```bash
cd examples/llmagent_with_streaming_progress_tool
cp ../mcp_tools/.env .env   # or write your own
# edit .env to set TRPC_AGENT_API_KEY / BASE_URL / MODEL_NAME
python run_agent.py
```

Expected output (abridged):

```
User: Please crawl https://example.com and fetch the first 5 pages.
[crawl_site] ⏳ {'status': 'started', 'url': 'https://example.com', 'max_pages': 5}
[crawl_site] ⏳ {'status': 'fetched', 'page': 1, 'total': 5, ...}
[crawl_site] ⏳ {'status': 'fetched', 'page': 2, 'total': 5, ...}
...
[tool-result] crawl_site → {'status': 'done', 'url': '...', 'pages_fetched': 5, ...}
Assistant: I crawled example.com and fetched 5 pages. ...
```

## How to consume progress events on the client side

Filter on `event.partial` + `custom_metadata.tool_progress` to detect a
progress chunk. The raw value the tool yielded is available in
`custom_metadata['payload']` (for `dict`/`BaseModel` yields) and as a JSON
string in `event.content.parts[0].text` for plain-text consumers.

```python
async for event in runner.run_async(...):
    meta = event.custom_metadata or {}
    if event.partial and meta.get("tool_progress"):
        print(meta["tool_name"], meta.get("payload") or event.get_text())
        continue
    # ...handle final events as usual
```

Notes:
- Progress events are NOT persisted into session history (they are partial).
- The LLM only ever sees the **last** yielded value as the tool response.
- If a batch contains a progress-streaming tool, the framework forces
  sequential tool execution to keep interim events in deterministic order,
  even if the agent has `parallel_tool_calls=True`.
