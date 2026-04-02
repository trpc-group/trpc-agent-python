# Team Leader 使用 Skills 示例

本示例演示 `TeamAgent` 的 Leader 除协调成员外，自身挂载 `SkillToolSet` 与 `skill_repository`：先 `skill_run` 生成要点文件，再委派 `researcher` / `writer` 成文。

## 关键特性

- Leader 工具列表包含 `FunctionTool(get_current_date)` 与 `skill_tool_set`
- 成员仍为 `search_web`、`check_grammar` 分工
- 技能目录内 `leader-research` 提供 `gather_points.sh` 等命令示例

## Agent 层级结构说明

- 根节点：`TeamAgent`（`content_team_with_skill`）
  - 成员：`researcher`（`LlmAgent`）、`writer`（`LlmAgent`）

## 关键代码解释

- `agent/agent.py`：`create_skill_tool_set(workspace_runtime_type="local")` 返回的工具集与 repository 挂在 Team 上
- `agent/tools.py`：封装 `create_skill_tool_set` 及成员侧工具
- `run_agent.py`：用户强制流程——先跑技能命令再委派成员

## 环境与运行

- Python 3.10+；仓库根目录 `pip install -e .`
- 配置 `TRPC_AGENT_API_KEY`、`TRPC_AGENT_BASE_URL`、`TRPC_AGENT_MODEL_NAME`
- 技能位于示例目录 `skills/`（可通过环境变量指定根路径）

```bash
cd examples/team_with_skill
python3 run_agent.py
```

## 运行结果（实测）


```
[START] team_with_skill
...
[content_team_with_skill] Tool: skill_run, Args: {'skill': 'leader-research', 'command': 'bash scripts/gather_points.sh "renewable energy and AI trends in current year" out/leader_notes.txt', ...
📊 [Tool Result: {... 'stdout': 'Notes generated at out/leader_notes.txt\n', 'exit_code': 0, ...}]
...
[content_team_with_skill] Tool: delegate_to_member, Args: {'member_name': 'researcher', ...
...
[END] team_with_skill (exit_code=0)
```

## 结果分析（是否符合要求）

符合本示例测试要求：`exit_code=0`；Leader 先完成 `skill_run` 再委派成员，输出中包含笔记文件内容与后续撰文，和「技能 + 团队」组合目标一致。

## 适用场景建议

- 团队负责人需要先跑标准化脚本/检索模板再分派下游角色时使用
- 可与容器型 `skill_run` 示例对照，选择本地或隔离执行环境
