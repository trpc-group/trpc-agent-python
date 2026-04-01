# LlmAgent带Schema的示例代码

- 运行用户档案分析Agent得示例（带工具）
- 运行不带工具的Agent示例（要求LLM有的JSON Output输出的能力）
- 运行把用户档案分析Agen作为AgentTool的代码示例

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
cd examples/llmagent_with_schema/
python3 run_agent.py
```
