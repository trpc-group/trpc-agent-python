# ToolSet 动态工具列表示例

本示例演示自定义 `WeatherToolSet`：在 `get_tools` 中根据 `invocation_context.session.state["user_type"]` 决定暴露「仅当前天气」或「含多日预报」的全部工具，从而模拟 BASIC / VIP 权限差异。

## 关键特性

- `BaseToolSet.initialize` 中注册 `get_current_weather`、`get_weather_forecast`
- `get_tools`：无 context 时保守返回单工具；`vip` 返回全部
- `run_agent.py` 对不同 `user_id` 建会话并写入 `state={"user_type": ...}`

## Agent 层级结构说明

- 根节点：`LlmAgent`（`weather_toolset_agent`），仅挂载 `WeatherToolSet` 实例
- 无子 Agent

## 关键代码解释

- `agent/tools.py`：`WeatherToolSet.get_tools` 读取 `session.state`
- `agent/agent.py`：`weather_toolset.initialize()` 后作为唯一 `tools` 传入
- `run_agent.py`：`test_scenarios` 循环 BASIC 与 VIP 两组问答

## 环境与运行

- Python 3.10+；仓库根目录 `pip install -e .`
- 配置 `TRPC_AGENT_API_KEY`、`TRPC_AGENT_BASE_URL`、`TRPC_AGENT_MODEL_NAME`（可用 `.env`）

```bash
cd examples/toolsets
python3 run_agent.py
```

## 运行结果（实测）


```
[START] toolsets
👤 User Type: BASIC
📝 Test 1: What's the current weather in Beijing?
🔧 [Invoke Tool: get_current_weather({'city': 'Beijing'})]
...
👤 User Type: VIP
📝 Test 1: Get the weather forecast for Beijing for the next 5 days
🔧 [Invoke Tool: get_weather_forecast({'city': 'Beijing', 'days': 5})]
...
✅ Weather ToolSet demo finished!
[END] toolsets (exit_code=0)
```

## 结果分析（是否符合要求）

符合本示例测试要求：`exit_code=0`；BASIC 仅触发当前天气工具，VIP 可调用预报工具，与动态 `get_tools` 逻辑一致。

## 适用场景建议

- 同一 Agent 需按租户、套餐或运行时策略切换可见工具时，用 `ToolSet` 集中封装
- 可扩展为从数据库或鉴权服务拉取权限再过滤工具列表
