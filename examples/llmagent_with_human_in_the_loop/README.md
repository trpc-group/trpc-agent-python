# LLM Agent 人机协同审批示例

本示例演示如何基于 `LongRunningFunctionTool` 实现 Human-in-the-Loop（人机协同）流程：当 Agent 遇到高风险操作时先进入 `pending_approval`，等待人工确认后再恢复执行。

## 关键特性

- **长时运行事件能力**：工具返回待审批状态后触发 `LongRunningEvent`
- **人工介入恢复执行**：通过构造 `FunctionResponse` 回填审批结果并继续原会话
- **主 Agent + 子 Agent 双路径验证**：既覆盖主 Agent 审批，也覆盖转发到子 Agent 的审批
- **高风险操作保护**：将“删除数据库 / 重启生产服务器”纳入人工审批门禁
- **流程可观测**：日志包含“检测长时事件、等待审批、恢复执行”完整链路

## Agent 层级结构说明

本例是“主 Agent + 子 Agent”协作示例：

```text
human_in_loop_agent (LlmAgent)
├── tool: human_approval_required (LongRunningFunctionTool)
└── system_operations_agent (LlmAgent)
    └── tool: check_system_critical_operation (LongRunningFunctionTool)
```

关键文件：

- `examples/llmagent_with_human_in_the_loop/agent/agent.py`：主/子 Agent 组装
- `examples/llmagent_with_human_in_the_loop/agent/tools.py`：审批类长时工具
- `examples/llmagent_with_human_in_the_loop/agent/prompts.py`：主/子 Agent 指令
- `examples/llmagent_with_human_in_the_loop/agent/config.py`：环境变量读取
- `examples/llmagent_with_human_in_the_loop/run_agent.py`：长时事件捕获与恢复执行

## 关键代码解释

这一节用于快速定位“触发审批、人工回填、恢复执行”三条链路。

### 1) 长时工具触发（`agent/tools.py`）

- `human_approval_required` 与 `check_system_critical_operation` 返回 `status=pending_approval`
- 两个函数均通过 `LongRunningFunctionTool` 包装，触发长时运行事件

### 2) 事件捕获与暂停（`run_agent.py`）

- `run_invocation(...)` 中检测 `LongRunningEvent`
- 打印 function name / response 并暂停等待人工介入

### 3) 人工回填与恢复（`run_agent.py`）

- 将审批结果改写为 `status=approved`
- 构造 `FunctionResponse` 作为 `resume_content` 再次调用 `run_invocation(...)`
- Agent 读取审批结果后继续给出最终执行结论

## 环境与运行

### 环境要求

- Python 3.10+（强烈建议 3.12）

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

### 环境变量要求

在 `examples/llmagent_with_human_in_the_loop/.env` 中配置（或通过 `export`）：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/llmagent_with_human_in_the_loop
python3 run_agent.py
```

## 运行结果（实测）

以下结果来自你提供的日志（`terminals/1.txt:158-268`）提炼版：

```text
Scenario 1: Main Agent - Database Deletion Approval
- 主 Agent 调用 human_approval_required，返回 pending_approval
- 检测到 Long-running operation，进入“Waiting for human intervention”
- 人工模拟返回 approved 后恢复执行，输出“数据库删除已批准”详情

Scenario 2: Sub-Agent - Critical System Operation
- 主 Agent 先 transfer_to_agent 到 system_operations_agent
- 子 Agent 调用 check_system_critical_operation，返回 pending_approval
- 人工模拟 approved 后恢复执行，输出“重启服务器已批准”详情
```

## 结果分析（是否符合要求）

结论：**符合本示例测试要求**。

- **长时事件触发正确**：两个场景都检测到 `Long-running operation`
- **人工审批闭环完整**：都经历了 pending → approved → resume
- **子 Agent 场景有效**：转发后在子 Agent 侧同样可触发并恢复长时流程
- **最终响应正确**：恢复后都输出了带审批信息的可执行结论

## 适用场景建议

- 需要对高风险操作设置人工审批门禁：适合使用本示例
- 需要验证“长时事件 + 恢复执行”链路：适合使用本示例
- 只验证单 Agent 工具调用主链路：建议使用 `examples/llmagent`
