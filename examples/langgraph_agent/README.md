# LangGraph Agent 基础示例

本示例演示 LangGraphAgent 的基础用法，展示如何使用 trpc_agent 框架集成 LangGraph 构建智能体。

## 功能说明

本示例展示了 LangGraphAgent 的核心功能:
- **LangGraph 集成**: 使用 LangGraph 构建智能体图
- **工具调用**: 通过 @langgraph_tool_node 装饰器定义工具
- **LLM 节点**: 通过 @langgraph_llm_node 装饰器定义 LLM 节点
- **流式输出**: 支持流式响应输出

## 环境要求

Python版本: 3.10+(强烈建议使用3.12)

## 运行方法

1. 下载并安装 trpc-agent-python

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

2. 在 `.env` 文件中设置环境变量(也可以通过export设置):
   - TRPC_AGENT_API_KEY
   - TRPC_AGENT_BASE_URL
   - TRPC_AGENT_MODEL_NAME

3. 运行示例:

```bash
cd examples/langgraph_agent/
python3 run_agent.py
```

## 开启sub_graph

可以取消，run_agent.py以及agent/agent.py里相应的注释来测试这个能力。

## 代码结构

```
langgraph_agent/
├── README.md           # 本文档
├── run_agent.py        # 入口文件，运行智能体
└── agent/
    ├── __init__.py     # 模块初始化
    ├── config.py       # 配置管理(读取环境变量)
    ├── tools.py        # 工具定义
    └── agent.py        # 智能体定义
```

## 预期输出

运行示例后，您将看到类似以下的输出:

```
============================================================
LangGraph Agent Demo
============================================================

User: Hello, who are you?
Assistant: Hello! I'm your helpful Assistant, here to assist you with friendly conversations, answer your questions, and perform calculations if needed. How can I help you today?Hello! I'm your helpful Assistant, here to assist you with friendly conversations, answer your questions, and perform calculations if needed. How can I help you today?

User: Please calculate 15 multiply 23.
Assistant: 
[Invoke Tool: calculate({'operation': 'multiply', 'a': 15, 'b': 23})]
[Tool Result: {'result': 'Calculation result: 15.0 multiply 23.0 = 345.0'}]
The result of 15 multiplied by 23 is **345**. Let me know if you need further assistance!The result of 15 multiplied by 23 is **345**. Let me know if you need further assistance!

User: Now divide the result by 5.
Assistant: 
[Invoke Tool: calculate({'operation': 'divide', 'a': 345, 'b': 5})]
[Tool Result: {'result': 'Calculation result: 345.0 divide 5.0 = 69.0'}]
{"content":"The result of 345 divided by 5 is **69**. Let me know if you'd like to perform any other calculations or need further assistance!","additional_kwargs":{},"response_metadata":{"finish_reason":"stop","model_name":"default","model_provider":"deepseek"},"type":"ai","name":null,"id":"lc_run--019bb03e-2f6b-7d0e-8c6e-6c6c0c6c6c6c","tool_calls":[],"invalid_tool_calls":[],"usage_metadata":{"input_tokens":500,"output_tokens":25,"total_tokens":525,"input_token_details":{},"output_token_details":{}}}{"content":"The result of 345 divided by 5 is **69**. Let me know if you'd like to perform any other calculations or need further assistance!","additional_kwargs":{},"response_metadata":{"finish_reason":"stop","model_name":"default","model_provider":"deepseek"},"type":"ai","name":null,"id":"lc_run--019bb03e-2f6b-7d0e-8c6e-6c6c0c6c6c6c","tool_calls":[],"invalid_tool_calls":[],"usage_metadata":{"input_tokens":500,"output_tokens":25,"total_tokens":525,"input_token_details":{},"output_token_details":{}}}

User: Thank you!
Assistant: You're welcome! If you have any more questions or need further assistance, feel free to ask. Have a great day! 😊You're welcome! If you have any more questions or need further assistance, feel free to ask. Have a great day! 😊

============================================================
Demo completed!
============================================================
```
