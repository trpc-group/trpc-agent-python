# TeamAgent 成员消息过滤示例

本示例演示 TeamAgent 的 `member_message_filter` 功能，展示如何控制团队成员的消息聚合方式。

## 功能说明

`member_message_filter` 参数允许自定义如何处理成员代理的消息：
- **keep_all_member_message**: 保留所有成员消息（默认行为）
- **keep_last_member_message**: 只保留最后一条成员消息
- **自定义过滤函数**: 实现自定义的消息过滤逻辑

本示例展示了数据分析场景，分析师执行多步骤分析任务。

## 环境要求

Python版本: 3.10+（强烈建议使用3.12）

## 运行方法

1. 下载并安装 trpc-agent-python

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
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
cd examples/team_member_message_filter/
python3 run_agent.py
```

## 预期行为

本示例演示成员消息过滤效果：

1. 用户请求 "Please analyze this year's regional sales performance and provide improvement recommendations"
2. 分析师获取各区域数据、计算统计指标、生成趋势分析
3. 消息过滤器处理成员消息，返回过滤后的结果
4. 领导基于过滤后的结果向用户回复

输出如下所示：

```
Member Message Filter Example
Demonstrates the effects of different member_message_filter filters

======================================================================
Member Message Filter Demo
======================================================================

User: Please analyze this year's regional sales performance and provide improvement recommendations
--------------------------------------------------

[analysis_team] Tool call: call_member

[analyst] Tool call: fetch_sales_data
[analyst] Tool response: East Region: Q1 sales $12M...

[analyst] Tool call: fetch_sales_data
[analyst] Tool response: South Region: Q1 sales $8M...

[analyst] Tool call: calculate_statistics
[analyst] Tool response: Statistical Analysis Results...

[analyst] Tool call: generate_trend_analysis
[analyst] Tool response: Trend Analysis...

==============================================
Got custom message_text:
[Filtered message content]
==============================================

[analyst] Based on the analysis, sales performance shows an overall upward trend...

[analysis_team] According to the data analysis team's report, this year's sales performance is generally good...

======================================================================
Demo completed!
======================================================================
```

## 过滤器说明

### keep_all_member_message

保留成员的所有消息历史，包括所有工具调用和响应。适用于需要完整上下文的场景。

### keep_last_member_message

只保留成员的最后一条消息。适用于只需要最终结论的场景，可以减少上下文长度。

### 自定义过滤器

```python
async def custom_keep_message(messages: List[Content]) -> str:
    # 自定义过滤逻辑
    message_text = await keep_last_member_message(messages)
    # 可以进行额外处理
    return message_text

# 全局设置
team = TeamAgent(
    member_message_filter=custom_keep_message,
    ...
)

# 或按成员设置
team = TeamAgent(
    member_message_filter={
        "analyst": custom_keep_message,
    },
    ...
)
```
