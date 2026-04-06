# 嵌套 TeamAgent 示例

本示例演示 `TeamAgent` 作为另一 `TeamAgent` 的成员：`project_manager` 委派给内层 `dev_team`，再由 `dev_team` 协调 `backend_dev` 与 `frontend_dev`，并行处理实现与文档角色。

## 关键特性

- 两层 `TeamAgent`：`project_manager` → `dev_team`（仍为 `TeamAgent`）
- 内层与外层均可 `share_member_interactions=True`
- 成员工具涵盖 API 设计、UI 设计与文档格式化等模拟能力

## Agent 层级结构说明

- 根节点：`TeamAgent`（`project_manager`）
  - 成员：`TeamAgent`（`dev_team`）
    - 成员：`backend_dev`（`LlmAgent`）、`frontend_dev`（`LlmAgent`）
  - 成员：`doc_writer`（`LlmAgent`）

## 关键代码解释

- `agent/agent.py`：先构造 `backend_dev`、`frontend_dev` 与 `dev_team`，再与 `doc_writer` 一并交给 `project_manager`
- `agent/tools.py`：`design_api`、`design_ui`、`format_docs` 等
- `run_agent.py`：多轮产品需求对话，打印跨层委派与工具调用

## 环境与运行

- Python 3.12；仓库根目录 `pip install -e .`
- 配置 `TRPC_AGENT_API_KEY`、`TRPC_AGENT_BASE_URL`、`TRPC_AGENT_MODEL_NAME`

```bash
cd examples/team_member_agent_team
python3 run_agent.py
```

## 运行结果（实测）

```txt
[START] team_member_agent_team
...
[project_manager] Tool: delegate_to_member, Args: {'member_name': 'dev_team', ...
[dev_team] Tool: delegate_to_member, Args: {'member_name': 'backend_dev', ...
[dev_team] Tool: delegate_to_member, Args: {'member_name': 'frontend_dev', ...
...
[END] team_member_agent_team (exit_code=0)
```

## 结果分析（是否符合要求）

符合本示例测试要求：`exit_code=0`；日志体现 project_manager → dev_team → 前后端成员的嵌套委派与工具输出，与层级设计一致。

## 适用场景建议

- 大型项目需「小队套小队」时使用嵌套 `TeamAgent`，避免单层成员过多
- 可按职能拆分外层（产品/项目经理）与内层（开发小队）
