# MultiAgent之Chain Agent的示例代码

## Chain Agent介绍
- **模式**：顺序执行，通过 output_key 将前一个Agent的输出传递给下一个Agent，实现数据的顺序传递和处理
- **适用场景**：需要按步骤处理的流水线任务，如文档处理（内容提取→翻译）
- **特点**：始终按照sub_agents列表中的顺序执行，无论输入如何，每个Agent专注处理流程中的一个环节

## 环境要求
Python版本: 3.10+（强烈建议使用3.12）

## 在trpc-agent-python框架代码下如何运行此代码示例

1. 下载trpc-agent-python代码并安装

```bash
git clone https://git.woa.com/trpc-python/trpc-python-agent/trpc-agent
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
cd examples/multi_agent_chain/
python3 run_agent.py
```
