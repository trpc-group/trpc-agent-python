# LLM Agent 运行次数限制示例

本示例演示如何通过 `RunConfig` 限制一次 Agent 调用中的 LLM 调用次数、循环次数和工具调用次数。

## 验证内容

示例包含三个独立场景：

- `max_llm_calls=1`：允许一次 LLM 调用，在第二次调用前抛出异常
- `max_iterations=1`：允许 Agent 执行一次循环，在第二次循环开始前抛出异常
- `max_tool_calls=1`：让 LLM 一次请求两个工具，超过限制后两个工具都不执行

每个场景使用一个单独的会话，并连续调用两次：

1. 第一次调用触发 `RunLimitException`
2. 第二次调用关闭限制，在同一个会话中询问 `What did we do previously?`

第二次调用可以正常完成，说明异常只会停止当前调用，不会关闭会话。

## 关键配置

```python
run_config = RunConfig(
    agent_limits={
        root_agent.name: AgentRunLimits(
            max_llm_calls=1,
            max_iterations=0,
            max_tool_calls=0,
        ),
    },
)
```

`agent_limits` 中的名称需要与 `agent.name` 完全一致。值为 `0` 表示不限制。

## 运行示例

先在 `.env` 中配置以下环境变量：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

然后运行：

```bash
cd examples/llmagent_with_limit
python3 run_agent.py
```

每个场景的预期结果如下：

```text
⛔ [max_llm_calls_exceeded: Agent 'weather_agent' reached max_llm_calls=1.]
✅ Invocation 1 raised the expected limit: configured=1, observed=2
✅ Invocation 2 continued and completed normally
```

另外两个场景会分别输出 `max_iterations_exceeded` 和 `max_tool_calls_exceeded`。
