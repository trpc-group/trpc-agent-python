# 自定义 Runner 示例

本示例演示如何在评测中**传入自定义 Runner**：推理由你提供的 Runner（含 Agent、SessionService 等）执行，打分逻辑仍由评测框架完成。

## 适用场景

- 复用已有会话服务（如 Redis、SQL）
- 与线上/本地部署使用同一 Runner 形态，统一鉴权、日志等
- 希望评测与真实运行环境一致

## 目录结构

```
custom_runner/
├── agent/
│   ├── __init__.py
│   ├── agent.py          # 天气 Agent（root_agent）
│   ├── config.py         # 模型配置
│   ├── test_config.json  # 评测指标配置
│   └── custom_runner_example.evalset.json  # 评测集
├── test_custom_runner.py # 构造 Runner 并调用 evaluate(..., runner=runner)
└── README.md
```

## 运行方式

在 **本目录** 下执行（需已配置 `TRPC_AGENT_API_KEY` 等环境变量）：

```bash
pytest test_custom_runner.py -v -s
```

## 要点

1. **构造 Runner**：使用 `Runner(app_name=..., agent=..., session_service=...)`，本示例使用 `InMemorySessionService()`，可按需替换为其他 SessionService。
2. **传入评测**：在 `AgentEvaluator.evaluate(..., runner=runner)` 或 `get_executer(..., runner=runner)` 中传入你的 Runner。
3. **session_input**：若评测用例中配置了 `session_input`，框架会按需在该 Runner 的会话中创建/更新会话。

更多说明见文档 [run_eval_pytest.md](../../../docs/evaluation/run_eval_pytest.md) 中的「自定义 Runner」小节。
