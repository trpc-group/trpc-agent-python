# MultiAgent之Parallel Agent的示例代码

## Parallel Agent介绍
- **模式**：并行执行多个Agent，各自独立处理相同输入
- **适用场景**：需要多角度分析的任务，如内容审查（质量检查+安全检查）
- **特点**：始终并行执行所有sub_agents，无论输入如何，可以提高效率，获得多维度结果

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
cd examples/multi_agent_parallel/
python3 run_agent.py
```
