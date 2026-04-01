# TeamAgent 作为 sub_agent 示例

本示例演示 TeamAgent 作为 sub_agent 下，展示TeamAgent内部的成员如何在完成任务后，将控制权转移给父agent或兄弟agent，有如下角色的Agent

- **Root Agent（coordinator者）**: 顶层LlmAgent，负责整体coordinator
  - **Sub Agent 1 (finance_team - TeamAgent)**: 处理财务相关任务
    - **analyst**: 成员agent，分析财务数据
  - **Sub Agent 2 (report_agent - LlmAgent)**: 处理报告生成任务

将会测试下面两种控制权转移场景：
- **Test 1**: finance_team完成分析后，transfer到父agent（coordinator）
- **Test 2**: finance_team完成分析后，transfer到兄弟agent（report_agent）

## 环境要求

Python版本: 3.10+（强烈建议使用3.12）

## 运行方法

1. 下载并安装 trpc-agent-python

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
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
cd examples/team_as_sub_agent/
python3 run_agent.py
```

参考输出如下：

```bash
TeamAgent as Sub-Agent Example
Demonstrates: TeamAgent -> transfer_to_agent -> Parent/Sibling

============================================================
TeamAgent as Sub-Agent Demo
============================================================

This demo shows how TeamAgent works as a sub_agent and
demonstrates transfer_to_agent mechanism:

Architecture:
  coordinator (Root)
  ├── finance_team (TeamAgent)
  │   ├── analyst (can transfer)
  │   └── auditor
  └── report_agent (sibling)

============================================================

============================================================
Test Scenario: Test 1: Transfer to Parent Agent
============================================================
Session ID: 4011309d...
Query: Please analyze Q4 financial data. Then back to coordinator.
------------------------------------------------------------

[coordinator] Tool: transfer_to_agent
  Args: {'agent_name': 'finance_team'}

[TRANSFER] coordinator -> finance_team
----------------------------------------

[coordinator] Tool Response: {'transferred_to': 'finance_team'}...

[TRANSFER] coordinator -> finance_team
----------------------------------------

[finance_team] Tool: delegate_to_member
  Args: {'member_name': 'analyst', 'task': 'Analyze the Q4 financial data and provide insights.'}

[finance_team] Tool Response: {'result': '{"marker":"__TEAM_DELEGATION__","action":"delegate_to_member","member_name":"analyst","t...

[analyst] Tool: analyze_financial_data
  Args: {'data_description': 'Q4 financial data'}

[analyst] Tool Response: {'result': 'Q4 Financial Analysis: Revenue increased by 15%, operating margin improved to 25%, stron...
The Q4 financial analysis reveals the following key insights:

1. **Revenue Growth**: Revenue increased by 15% compared to the previous quarter.
2. **Operating Margin**: The operating margin improved significantly to 25%.
3. **Regional Performance**: All regions demonstrated strong performance during this quarter.

Would you like me to transfer this analysis to another agent for further action? If so, please specify the target agent.
[finance_team] The user's task was to analyze the Q4 financial data and then return to the coordinator. The analysis has been completed, and the insights have been provided. 

I will now transfer the analysis back to the coordinator for further discussion or action.
[finance_team] Tool: transfer_to_agent
  Args: {'agent_name': 'coordinator'}

[TRANSFER] finance_team -> coordinator
----------------------------------------

[finance_team] Tool Response: {'transferred_to': 'coordinator'}...

[TRANSFER] finance_team -> coordinator
----------------------------------------
The user's task to analyze the Q4 financial data has been completed, and the insights have been provided. The next step, as per the user's request, is to return to the coordinator. Since this has already been done, the task is now finished. 

If there are any further actions or discussions required, the coordinator will handle them.
[coordinator] Tool: transfer_to_agent
  Args: {'agent_name': 'coordinator'}

[TRANSFER] coordinator -> coordinator
----------------------------------------

[coordinator] Tool Response: {'transferred_to': 'coordinator'}...

[TRANSFER] coordinator -> coordinator
----------------------------------------
The Q4 financial analysis has been completed with the following key insights:

1. **Revenue Growth**: Revenue increased by 15% compared to the previous quarter.
2. **Operating Margin**: The operating margin improved significantly to 25%.
3. **Regional Performance**: All regions demonstrated strong performance during this quarter.

If you need any further actions or summaries, feel free to let me know!
============================================================

============================================================
Test Scenario: Test 2: Transfer to Sibling Agent
============================================================
Session ID: ac4960a2...
Query: Please analyze Q4 financial data and then transfer to report_agent to generate a report
------------------------------------------------------------

[coordinator] Tool: transfer_to_agent
  Args: {'agent_name': 'finance_team'}

[TRANSFER] coordinator -> finance_team
----------------------------------------

[coordinator] Tool Response: {'transferred_to': 'finance_team'}...

[TRANSFER] coordinator -> finance_team
----------------------------------------

[finance_team] Tool: delegate_to_member
  Args: {'member_name': 'analyst', 'task': 'Analyze the Q4 financial data and provide insights.'}

[finance_team] Tool Response: {'result': '{"marker":"__TEAM_DELEGATION__","action":"delegate_to_member","member_name":"analyst","t...

[analyst] Tool: analyze_financial_data
  Args: {'data_description': 'Q4 financial data'}

[analyst] Tool Response: {'result': 'Q4 Financial Analysis: Revenue increased by 15%, operating margin improved to 25%, stron...
The analysis of the Q4 financial data reveals the following key insights:

1. **Revenue Growth**: Revenue increased by 15% compared to the previous quarter or the same quarter last year (context not specified).
2. **Operating Margin Improvement**: The operating margin improved to 25%, indicating better cost management or higher profitability.
3. **Regional Performance**: All regions showed strong performance, contributing to the overall positive results.

Would you like me to transfer this analysis to another agent for further action or reporting? If so, please specify the target agent.
[finance_team] Tool: transfer_to_agent
  Args: {'agent_name': 'report_agent'}

[TRANSFER] finance_team -> report_agent
----------------------------------------

[finance_team] Tool Response: {'transferred_to': 'report_agent'}...

[TRANSFER] finance_team -> report_agent
----------------------------------------
The user's task has been completed as follows:

1. The Q4 financial data was analyzed by the analyst, revealing key insights.
2. The analysis was transferred to the `report_agent` for report generation.

If there are no further instructions or tasks, the process is complete. Let me know if you'd like to proceed with anything else!
[report_agent] Tool: generate_report
  Args: {'content': 'Q4 Financial Analysis Report\n\nKey Insights:\n1. **Revenue Growth**: Revenue increased by 15% compared to the previous quarter or the same quarter last year (context not specified).\n2. **Operating Margin Improvement**: The operating margin improved to 25%, indicating better cost management or higher profitability.\n3. **Regional Performance**: All regions showed strong performance, contributing to the overall positive results.\n\nConclusion:\nThe Q4 financial results reflect a robust performance across all key metrics, with significant revenue growth and improved profitability.'}

[report_agent] Tool Response: {'result': '\n=== Financial Report ===\nQ4 Financial Analysis Report\n\nKey Insights:\n1. **Revenue ...
Here is the generated financial report for Q4:

---

### Financial Report  
**Q4 Financial Analysis Report**  

**Key Insights:**  
1. **Revenue Growth**: Revenue increased by 15% compared to the previous quarter or the same quarter last year (context not specified).  
2. **Operating Margin Improvement**: The operating margin improved to 25%, indicating better cost management or higher profitability.  
3. **Regional Performance**: All regions showed strong performance, contributing to the overall positive results.  

**Conclusion:**  
The Q4 financial results reflect a robust performance across all key metrics, with significant revenue growth and improved profitability.  

---

Let me know if you'd like any modifications or further details!
============================================================

============================================================
Demo completed!
============================================================
```
