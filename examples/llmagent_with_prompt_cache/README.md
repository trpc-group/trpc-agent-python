# Prompt Cache 示例

本示例演示如何在 OpenAI、Anthropic 上，以及其他经 LiteLLM 接入的兼容端点上，使用 SDK 统一的 prompt cache。
所有场景使用同一个「天气管家」Agent，区别仅在于所选的模型类和缓存配置。

运行这个例子后，在支持 prompt cache 的 API 上，期望能够看到较高的 prompt cache 命中率，以及随轮次增长的 TTFT 改善（Turn 2 起缓存命中后响应明显变快）。本示例中的 TTFT 指从请求开始到第一个有效生成 token 出现的耗时；无论该 token 属于普通 message 还是 tool call 都计入。

---

## 目录结构

```
llmagent_with_prompt_cache/
├── agent/
│   ├── agent.py       ← 三个工厂函数 + 自动探测 helper
│   ├── config.py      ← 环境变量 helper
│   ├── prompts.py     ← 长系统提示词（约 4 900 token）
│   └── tools.py       ← 模拟天气工具
│
├── run_agent.py       ← 根据环境变量自动探测 provider 并运行 demo 循环
│
└── .env               ← 环境变量配置（三个 provider 均在此注释分段）
```

---

## 环境与运行

### 环境要求

- Python 3.12

### 安装步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
python3 -m venv .venv
source .venv/bin/activate
pip3 install -e .
```

### 环境变量要求

在 [examples/llmagent_with_prompt_cache/.env](./.env) 中填入凭证：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`

### 运行命令

```bash
cd examples/llmagent_with_prompt_cache
python3 run_agent.py       # 根据 .env 的模型名字自动选择 provider
```

---

## FQA
### 缓存命中不稳定（命中后又未命中又命中）

在负载均衡的代理部署下属于正常现象。每个后端实例都有独立的 KV 缓存。无论其他实例
预热了多少，落到冷实例上的请求总会显示未命中。把脚本多跑几次即可提高命中率。
