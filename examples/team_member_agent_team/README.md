# TeamAgent 嵌套团队示例

本示例演示 TeamAgent 作为另一个 TeamAgent 的成员，实现层级化的团队结构。

## 功能说明

本示例展示了一个软件开发项目的层级团队结构：

```
project_manager (TeamAgent - 顶层)
├── dev_team (TeamAgent - 作为成员的嵌套TeamAgent)
│   ├── backend_dev (LlmAgent - 后端开发)
│   └── frontend_dev (LlmAgent - 前端开发)
└── doc_writer (LlmAgent - 文档编写)
```

### 团队角色

- **project_manager（项目经理）**: 顶层 TeamAgent，协调开发团队和文档编写
- **dev_team（开发团队）**: 嵌套的 TeamAgent，协调后端和前端开发
  - **backend_dev（后端开发）**: 负责 API 和服务端实现（使用 design_api 工具）
  - **frontend_dev（前端开发）**: 负责 UI 和客户端实现（使用 design_ui 工具）
- **doc_writer（文档编写）**: 负责技术文档编写（使用 format_docs 工具）

### 核心特性

1. **嵌套 TeamAgent**: dev_team 作为 project_manager 的成员，本身也是一个 TeamAgent
2. **层级委派**: project_manager -> dev_team -> [backend_dev, frontend_dev]
3. **隔离上下文**: 嵌套的 TeamAgent 使用临时上下文，不影响父级的 session.state
4. **HITL 限制**: 作为成员的 TeamAgent 不能触发人机交互（Human-in-the-loop）

## 环境要求

Python版本: 3.10+（强烈建议使用3.12）

## 运行方法

1. 下载并安装 trpc-agent-python

```bash
git clone https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent
cd trpc-agent
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

2. 在 `.env` 文件中设置环境变量（也可以通过export设置）:
   - TRPC_AGENT_API_KEY
   - TRPC_AGENT_BASE_URL
   - TRPC_AGENT_MODEL_NAME

3. 运行示例:

```bash
cd examples/team_member_agent_team/
python3 run_agent.py
```

## 预期行为

当用户请求 "Please implement a user authentication feature with login UI and API" 时：

1. **project_manager** 分析需求，委派给 dev_team 处理技术实现
2. **dev_team**（嵌套 TeamAgent）接收任务后：
   - 委派给 backend_dev 设计认证 API
   - 委派给 frontend_dev 设计登录 UI
   - 整合技术方案返回
3. **project_manager** 委派给 doc_writer 编写文档
4. **project_manager** 综合所有结果，返回最终响应

示例输出：

```
Hierarchical Team Example
Demonstrates TeamAgent as member of another TeamAgent
Structure: project_manager -> dev_team -> [backend_dev, frontend_dev]
                           -> doc_writer

======================================================================
Hierarchical Team Demo - TeamAgent as Member
======================================================================

Session ID: abc12345...

This demo shows nested TeamAgent structure:
  project_manager (TeamAgent)
    -> dev_team (TeamAgent as member)
       -> backend_dev (LlmAgent)
       -> frontend_dev (LlmAgent)
    -> doc_writer (LlmAgent)

----------------------------------------------------------------------

[Turn 1] User: Please implement a user authentication feature with login UI and API
--------------------------------------------------

[project_manager] Tool: delegate_to_member, Args: {'member_name': 'dev_team', 'task': '...'}

[dev_team] Tool: delegate_to_member, Args: {'member_name': 'backend_dev', 'task': '...'}

[backend_dev] Tool: design_api, Args: {'feature': 'user authentication'}

[backend_dev] API designed with JWT authentication...

[dev_team] Tool: delegate_to_member, Args: {'member_name': 'frontend_dev', 'task': '...'}

[frontend_dev] Tool: design_ui, Args: {'feature': 'login'}

[frontend_dev] UI components designed with React...

[dev_team] Technical implementation plan ready...

[project_manager] Tool: delegate_to_member, Args: {'member_name': 'doc_writer', 'task': '...'}

[doc_writer] Tool: format_docs, Args: {'content': '...'}

[doc_writer] Documentation formatted...

[project_manager] Project summary: Authentication feature implemented with...

======================================================================
Demo completed!
======================================================================
```

## 技术说明

### 嵌套 TeamAgent 的工作原理

当 TeamAgent 作为另一个 TeamAgent 的成员时：

1. **上下文隔离**: 嵌套的 TeamAgent 使用临时的 `TeamRunContext`，不会修改父级的 `session.state`
2. **消息传递**: 父级通过 `override_messages` 控制嵌套 TeamAgent 的输入
3. **HITL 限制**: 嵌套的 TeamAgent 及其成员不能触发 `LongRunningEvent`（人机交互），否则会抛出 `RuntimeError`

### 相关代码

- `TeamAgent._run_async_impl()`: 检测 member mode 并使用临时上下文
- `TeamAgent._execute_delegation()`: 传递 `is_member_mode` 和 `context_lock` 参数
- `TeamRunContext.from_state()`: 从 session.state 恢复上下文（仅 root mode）
