# 动态技能工具示例

本示例演示在同一 `LlmAgent` 上同时使用 `SkillToolSet` 与 `DynamicSkillToolSet`：先通过技能元工具加载 `weather-tools`，再按需暴露天气与城市查询等工具并完成多步问答。

## 关键特性

- `DynamicSkillToolSet`：`only_active_skills=True` 时仅对已加载技能开放声明过的工具
- 与 `SkillToolSet` 共用同一 `skill_repository`
- 示例技能 `weather-tools` 提供 `get_current_weather`、`get_weather_forecast`、`search_city_by_name` 等

## Agent 层级结构说明

- 根节点：`LlmAgent`（`skill_run_agent`），工具列表为 `[skill_tool_set, dynamic_tool_set]`，并绑定 `skill_repository`
- 无子 Agent

## 关键代码解释

- `agent/agent.py`：`create_skill_tool_set` 与 `create_skill_dynamic_tool_set` 组合后传入 `LlmAgent`
- `agent/tools/_dynamic.py`：`DynamicSkillToolSet` 配置 `available_tools`（含 `FunctionTool(ask_name_information)` 等）
- `run_agent.py`：单条长提示触发 `skill_list` → `skill_load` → `skill_select_tools` → 多次工具调用

## 环境与运行

- Python 3.10+；仓库根目录 `pip install -e .`
- 配置 `TRPC_AGENT_API_KEY`、`TRPC_AGENT_BASE_URL`、`TRPC_AGENT_MODEL_NAME`
- 技能目录默认在示例内 `skills/`，可通过环境变量 `SKILLS_ROOT` 覆盖

```bash
cd examples/skills_with_dynamic_tools
python3 run_agent.py
```

## 运行结果（实测）

```txt
[START] skills_with_dynamic_tools
...
DynamicSkillToolSet initialized: 3 tools, 0 toolsets, only_active_skills=True
🔧 [Invoke Tool:: skill_load({'skill_name': 'weather-tools'})]
...
🔧 [Invoke Tool:: get_current_weather({'city': 'Beijing'})]
...
📊 [Tool Result: {'city': 'Beijing', 'temperature': 22, ...}]
...
[END] skills_with_dynamic_tools (exit_code=0)
```

## 结果分析（是否符合要求）

符合本示例测试要求：`exit_code=0`；动态工具集初始化与 `weather-tools` 加载、多工具调用及最终回答均与日志一致，超出技能能力的问题（如人物信息）被合理拒答或说明。

## 适用场景建议

- 工具数量随已加载技能变化、需避免一次性暴露全部工具时，采用 `DynamicSkillToolSet`
- 可与固定 `SkillToolSet` 并存，由模型先加载技能再选工具
