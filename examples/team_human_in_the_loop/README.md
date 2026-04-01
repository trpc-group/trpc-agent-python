# TeamAgent 人机协作示例

本示例演示 TeamAgent 的 Human-in-the-Loop (HITL) 功能，团队领导可以触发需要人工输入的审批请求。

## 功能说明

Human-in-the-Loop 是一种需要人工干预的工作流模式：
- 用户请求需要审批的内容
- 领导通过 LongRunningFunctionTool 触发 HITL
- 系统暂停并产生 LongRunningEvent
- 用户提供审批（模拟）
- 团队恢复并完成任务

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
cd examples/team_human_in_the_loop/
python3 run_agent.py
```

## 预期行为

本示例演示 HITL 审批流程：

1. 用户请求 "Please help me search for AI-related information, then 'publish' a report"
2. 助手搜索信息后，触发发布审批请求
3. 系统暂停，等待人工审批
4. 人工提供审批后，系统恢复执行
5. 团队完成任务并返回结果

输出如下所示：

```
HITL Team Example
Demonstrates Human-in-the-Loop with TeamAgent

============================================================
HITL Team Demo - Human-in-the-Loop
============================================================

[User] Please help me search for AI-related information, then 'publish' a report
----------------------------------------
Assistant: 
[hitl_team] Tool: call_member
[hitl_team] Tool Result: ...

[assistant] Tool: search_info
[assistant] Tool Result: Information about 'AI': This is an important research...

[assistant] I have found information about AI...

========================================
HITL TRIGGERED!
Function: request_approval
Args: {'content': 'AI report content...', 'reason': 'Need to publish report'}
Response: {'status': 'pending', 'approval_id': '...'}
========================================
Waiting for human intervention...
----------------------------------------
Human intervention simulation...
Human provides approval: {'status': 'approved', 'approved_by': 'admin', ...}
----------------------------------------
Resuming team execution...
Assistant: 
[hitl_team] The report has been approved and published successfully...

============================================================
HITL Flow Completed!
============================================================
```
