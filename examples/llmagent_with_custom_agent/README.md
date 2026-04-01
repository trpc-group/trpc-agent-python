# Custom Agent 智能文档处理示例

## 环境要求
Python版本: 3.10+（强烈建议使用3.12）

## 在trpc-agent-python框架代码下如何运行此代码示例

1. 下载trpc-agent-python代码并安装

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

2. 运行此代码示例

在 `.env` 文件中设置使用 LLM 相关的变量（也可以通过export设置）:
- TRPC_AGENT_API_KEY
- TRPC_AGENT_BASE_URL
- TRPC_AGENT_MODEL_NAME

然后运行下面的命令：

```bash
cd examples/llmagent_with_custom_agent/
python3 run_agent.py
```

## 示例说明

本示例展示了如何使用 Custom Agent（自定义Agent）实现复杂的条件逻辑和动态Agent编排：

- **条件逻辑** - 根据文档类型（simple/complex/technical）选择不同的处理策略
- **状态管理** - 在Agent间通过session state传递分析结果
- **动态决策** - 基于处理结果决定是否需要质量验证
- **ChainAgent** - 使用ChainAgent封装复杂文档的分析→处理流程
