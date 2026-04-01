# MultiAgent之Sub Agents的示例代码

### Sub Agents介绍
- **模式**：层次化Agent结构，父Agent可以转发任务给专门的子Agent
- **适用场景**：复杂任务分解，如智能客服（路由Agent→专业咨询Agent→问题解决Agent）
- **特点**：层次结构，任务分发，专业化处理

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
cd examples/multi_agent_subagent/
python3 run_agent.py
```
