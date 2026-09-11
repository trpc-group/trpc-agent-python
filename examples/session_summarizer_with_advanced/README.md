# Advanced Session Summarizer 示例

本示例验证 `AdvancedAutoCompactSummarizer` 的模型调用前压缩流程。示例执行 5 轮真实多轮对话，随着历史增长，内置 Model Filter 会在每次请求模型前检查并压缩上下文。

## 验证内容

- 模型显式配置 `AdvancedAutoCompactSummarizerFilter`
- 使用较小的字符阈值，在前几轮内触发自动压缩
- 压缩后的模型可见窗口以 summary event 开头
- 被替换的原始 events 移入 `historical_events`
- Session Memory 写入 `session.state`
- 压缩后对话继续进行，模型仍可依据摘要回答历史事实

## 为什么必须用真实多轮对话

压缩边界基于模型请求中的 content 数量计算（`keep_recent_contents`）。框架会把非当前 Agent 产出的历史事件转换为 user 角色，并合并相邻同角色 content。手工塞入 `author="assistant"` 的伪造事件会被合并成单条 user content，导致找不到压缩边界并报 `Not enough model contents to compact`。因此示例通过真实回合累积 user/model 交替历史。

## 组件关系

```text
OpenAIModel
└── AdvancedAutoCompactSummarizerFilter（模型调用前触发）

InMemorySessionService
└── AdvancedAutoCompactSummarizerManager
    └── AdvancedAutoCompactSummarizer
```

Filter 必须显式安装到模型上，Manager 不会自动修改 Agent 或 Model。

## 环境要求

- Python3.10+，推荐 Python3.12

## 构建步骤

```bash
git clone https://github.com/trpc-group/trpc-agent-python.git
cd trpc-agent-python
./build.sh
source .venv/bin/activate
```

## 运行步骤

### 配置环境变量

在当前目录的 `.env` 中配置（或通过 `export` 设置）：

```dotenv
TRPC_AGENT_API_KEY=your-api-key
TRPC_AGENT_BASE_URL=https://your-openai-compatible-endpoint/v1
TRPC_AGENT_MODEL_NAME=your-model-name
```

### 运行命令

```bash
cd examples/session_summarizer_with_advanced
python3 run_agent.py
```

## 预期结果

```text
After turn 2
  active events: 4
  historical events: 0
  active window starts with summary: False
  session memory persisted: True

After turn 3
  active events: 5
  historical events: 3
  active window starts with summary: True
  session memory persisted: True

After turn 5
  active events: 5
  historical events: 10
  active window starts with summary: True
  session memory persisted: True

PASS: compaction ran on turn(s) [3, 4, 5].
```

关键现象是 `active events` 稳定在一个小窗口，而 `historical events` 持续增长——说明模型可见上下文被压缩，原始事件仍可完整追溯。具体轮次和数量取决于模型回复长度。若模型未按压缩提示词返回 `<summary>` 块，示例会在结束时明确报错。

## 调整阈值

示例为便于观察而关闭 token 模式，使用 `trigger_chars=4000`。生产环境可启用 `TokenContextTrackerConfig` 并设置模型上下文窗口，或根据业务规模提高字符阈值。
