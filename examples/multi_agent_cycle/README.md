# MultiAgent之Cycle Agent的示例代码

## Cycle Agent介绍
- **模式**：在多个Agent间循环执行，直到满足退出条件
- **适用场景**：需要多轮迭代优化的任务场景，如内容创作（生成→评估→改进→再评估）
- **特点**：按照固定的循环模式迭代执行，持续改进，直到满足明确的退出条件

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
cd examples/multi_agent_cycle/
python3 run_agent.py
```
