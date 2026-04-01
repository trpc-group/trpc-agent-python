# TeamAgent 内容团队示例

本示例演示 TeamAgent 的 Coordinate 模式，其中团队领导将任务委派给特定成员，并综合他们的响应。

## 功能说明

TeamAgent 是一种团队协作模式，包含：
- **Leader（领导）**: 协调研究和写作任务
- **Researcher（研究员）**: 擅长查找信息（使用 search_web 工具）
- **Writer（写手）**: 擅长撰写清晰、引人入胜的内容（使用 check_grammar 工具）

本示例展示了 2 轮对话，演示多轮对话支持。

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
cd examples/team/
python3 run_agent.py
```

## 预期行为

本示例在同一个会话中发送2条消息：

1. "Please write a short article about renewable energy" → Leader 先委派给 Researcher 搜索信息，再委派给 Writer 撰写内容
2. "Please help me add some content about AI" → Leader 继续协调 Researcher 和 Writer 完成任务

输出如下所示：

```
Content Team Example
Demonstrates coordinate mode: Leader -> Researcher -> Writer

============================================================
Content Team Demo - Coordinate Mode
============================================================

[Turn 1] User: Please write a short article about renewable energy
----------------------------------------
[content_team] Tool: get_current_date, Args: {}

[content_team] Tool Response: FunctionResponse(name='get_current_date'...

[content_team] Tool: call_member({'member_name': 'researcher', 'task_instruction': 'Search for the latest information about renewable energy'})

[researcher] Tool: search_web, Args: {'query': 'renewable energy'}

[researcher] Tool Response: FunctionResponse(name='search_web'...

[researcher] Research findings: In 2024, global renewable energy share reached 30%, solar costs dropped 89%...

[content_team] Tool: call_member({'member_name': 'writer', 'task_instruction': 'Write a short article about renewable energy based on the research results'})

[writer] Tool: check_grammar, Args: {'text': '...'}

[writer] Renewable energy is transforming the global energy landscape...

[content_team] Here is the short article about renewable energy...

[Turn 2] User: Please help me add some content about AI
----------------------------------------
...

============================================================
```
