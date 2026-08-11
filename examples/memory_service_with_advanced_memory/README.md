# Advanced Memory

## Advanced Memory 简介

`Advanced Memory` 是一套面向 Agent 的本地化记忆与上下文管理机制，重点增强
Agent 在长期信息沉淀和超长对话处理方面的能力：

- **本地化持久存储**：记忆和上下文数据以本地文件形式持久化，存储位置、数据边界
  和组织方式清晰可控，适合本地开发、调试、迁移和审计。
- **更强的长期记忆能力**：支持将对话中的稳定事实、用户偏好和重要经验主动沉淀为
  可组织、可更新、可跨 Session 使用的长期记忆，而不是简单堆积历史消息。
- **分层记忆管理**：分别管理原始对话、Session 级记忆和跨 Session 长期记忆，让不同
  类型的信息以合适的粒度参与后续推理。
- **上下文管理**：根据上下文规模、信息类型和使用情况，对历史消息、工具结果及记忆
  内容进行统一治理，在保留关键信息的同时控制模型输入规模。
- **上下文压缩**：支持对历史上下文和工具结果进行渐进式裁剪、压缩和摘要，降低长
  对话导致的上下文膨胀以及超出模型窗口限制的风险。
- **结构化记忆提取**：从持续增长的对话中提取结构化信息，形成更稳定、更易维护的
  Session Memory，提升后续对话对历史信息的利用效率。

本示例演示如何使用 `AdvancedMemorySessionService`。它把 Session 持久化和
Advanced Memory 上下文管理整合到一个 SessionService 中，用户不需要显式调用
`setup_advanced_memory()`，也不需要再创建 `InMemorySessionService`。

## 示例流程

脚本使用同一个 Runner 执行多个 Session：

1. `session-1` 连续输入多轮 Python 开发偏好。
2. 低阈值配置会触发 session memory 提取，并写入 `session_memory.md`。
3. `session-1` 请求总结已经学习到的开发偏好。
4. `session-2` 查询长期记忆，验证不同 Session 共享同一个 `MEMORY/`。

## 使用方式

```python
from pathlib import Path

from trpc_agent_sdk.memory import AdvancedMemoryConfig
from trpc_agent_sdk.sessions import AdvancedMemorySessionService
from trpc_agent_sdk.runners import Runner

session_service = AdvancedMemorySessionService(
    config=AdvancedMemoryConfig(
        root_dir=Path(__file__).resolve().parent,
    )
)

runner = Runner(
    app_name="advanced_memory_demo",
    agent=agent,
    session_service=session_service,
)
```

`Runner` 检测到 `AdvancedMemorySessionService` 后会自动完成 Advanced Memory
绑定，包括：

- transcript 持久化
- session memory 提取
- 长期记忆 tools：`save_memory`、`read_memory`、`list_memory_index`
- `HistorySnip`
- `Microcompact`
- `AutoCompact`
- `ToolResultBudget`

`AdvancedMemoryConfig` 默认已经启用这些能力。示例中额外降低了
session-memory 的阈值，只是为了用较少的对话轮数演示提取流程；生产环境可以
删除这些阈值配置，使用默认值。

## 数据目录

运行后，数据默认写入当前示例目录：

```text
MEMORY/
├── MEMORY.md
└── *.md                         # 长期记忆详情

SESSION/
├── _state.json                  # app/user 级 state
├── session-1/
│   ├── session.json             # Session 元数据和 session state
│   ├── transcript.jsonl         # 原始 Events 和 checkpoint
│   ├── session_memory.md        # 结构化 Session 记忆
│   └── tool-results/            # 超大工具结果
└── session-2/
    ├── session.json
    ├── transcript.jsonl
    └── session_memory.md
```

其中：

- `session.json` 保存 Session 元数据和状态，不保存完整 Events。
- `transcript.jsonl` 是追加写入的原始事件日志，可用于恢复 Session。
- `session_memory.md` 是根据 transcript 提取的结构化摘要。
- `MEMORY/` 保存跨 Session 使用的长期记忆。

## 运行

先在本目录创建 `.env`，然后填写模型配置：

```bash
cd examples/memory_service_with_advanced_memory
python3 run_agent.py
```

需要的环境变量：

- `TRPC_AGENT_API_KEY`
- `TRPC_AGENT_BASE_URL`
- `TRPC_AGENT_MODEL_NAME`
- `TRPC_AGENT_MODEL_CONTEXT_WINDOW_TOKENS`（可选，模型总上下文窗口大小，单位为 token）
- `TRPC_AGENT_MAX_OUTPUT_TOKENS`（可选，模型最大输出窗口大小，单位为 token）

`.env` 中留空的变量不会覆盖默认值；如果同时在 Python 中传入
`model_context_window_tokens` 或 `max_output_tokens`，Python 显式配置优先。

如果配置了模型上下文窗口，Advanced Memory 会用
`TRPC_AGENT_MODEL_CONTEXT_WINDOW_TOKENS - TRPC_AGENT_MAX_OUTPUT_TOKENS`
作为可用于输入内容的窗口；两个变量都留空时使用字符数阈值。

## `AdvancedMemoryConfig` 配置项

下面列出当前所有可直接传入 `AdvancedMemoryConfig` 的配置项。**没有特殊需求时，
只设置 `root_dir` 即可**；示例中的值均为默认值。

```python
session_service = AdvancedMemorySessionService(
    config=AdvancedMemoryConfig(
        root_dir=Path(__file__).resolve().parent, # 当前示例目录
        # root_dir=Path(
        # "/data/workspace/trpc-agent-python/examples/memory_service_with_advanced_memory"
        # ),
        # Optional
        enabled=True,                             # 总开关和存储路径
        memory_dir_name="MEMORY",                 # 长期记忆目录
        session_dir_name="SESSION",               # Session 数据目录
        memory_index_name="MEMORY.md",            # 长期记忆索引文件
        transcript_name="transcript.jsonl",       # transcript 文件
        session_memory_name="session_memory.md",  # Session 摘要文件
        encoding="utf-8",                         # 文件编码
        transcript_fsync=False,                   # transcript 写入后是否 fsync

        # 长期记忆
        memory_index_max_lines=200,               # 注入 prompt 的索引最大行数
        memory_index_max_bytes=25_000,            # 注入 prompt 的索引最大字节数
        long_term_memory_injection_enabled=True,  # 是否注入 MEMORY.md

        # 工具结果
        tool_result_max_chars=50_000,              # 单个工具结果最大字符数
        tool_results_per_message_max_chars=200_000,  # 单条消息工具结果总上限
        tool_result_preview_chars=2_000,           # 超限结果的预览字符数

        # HistorySnip
        history_snip_enabled=True,                # 是否压缩过长历史
        history_snip_trigger_chars=600_000,        # 触发阈值
        history_snip_target_chars=400_000,         # 压缩目标
        history_snip_keep_recent=5,                # 保留最近的完整消息数
        history_snip_tool_names=(                  # 可处理的工具名称
            "Read", "Bash", "Grep", "Glob",
            "WebSearch", "WebFetch", "Edit", "Write",
        ),

        # Token 上下文预算
        # 这两个值也可以通过 .env 配置；显式传参优先于环境变量。
        # model_context_window_tokens=131072,      # 显式设置后覆盖环境变量
        # max_output_tokens=8192,                  # 显式设置后覆盖环境变量
        # 如果省略这两行，则分别读取 .env；未配置时默认 None 和 0。
        token_warning_ratio=0.85,                  # 告警比例
        token_autocompact_ratio=0.90,              # 自动压缩比例
        token_blocking_ratio=0.95,                 # 阻止继续增加上下文的比例
        token_estimator=None,                      # 可选：自定义 token 估算器
        context_window_resolver=None,              # 可选：自定义窗口解析器

        # Session Memory
        session_memory_enabled=True,               # 是否启用 Session 摘要
        session_memory_initial_chars=40_000,       # 首次提取字符阈值
        session_memory_update_chars=20_000,        # 后续更新字符阈值
        session_memory_initial_tokens=10_000,      # 首次提取 token 阈值
        session_memory_update_tokens=5_000,        # 后续更新 token 阈值
        session_memory_tool_calls_between_updates=3,  # 两次更新间的工具调用数
        session_memory_prompt_max_chars=200_000,   # 摘要请求最大字符数
        session_memory_request_overhead_tokens=2_048,  # 请求预留 token
        session_memory_section_max_chars=8_000,    # 单个摘要 section 最大字符数
        session_memory_total_max_chars=54_000,     # 摘要总最大字符数
        session_memory_wait_timeout_seconds=15.0, # 等待摘要 Agent 的超时时间

        # AutoCompact
        autocompact_enabled=True,                  # 是否启用自动压缩
        autocompact_trigger_chars=700_000,         # 触发阈值
        autocompact_target_chars=350_000,          # 压缩目标
        autocompact_blocking_chars=780_000,        # 阻止继续增加上下文的阈值
        autocompact_keep_recent_contents=8,        # 保留最近内容数
        autocompact_max_failures=3,                # 最大连续失败次数
        autocompact_summary_input_max_chars=600_000,  # 摘要 Agent 输入上限
        autocompact_summary_retries=3,             # 摘要 Agent 重试次数

        # Microcompact
        microcompact_enabled=True,                # 是否启用工具结果微压缩
        microcompact_gap_seconds=3_600.0,          # 工具结果时间间隔阈值
        microcompact_trigger_count=20,             # 触发工具结果数量
        microcompact_keep_recent=5,                # 保留最近工具结果数
        microcompact_tool_names=(                  # 可处理的工具名称
            "Read", "Bash", "Grep", "Glob",
            "WebSearch", "WebFetch", "Edit", "Write",
        ),

        # Advanced Memory preload
        preload_memory_enabled=False,             # 是否自动预加载相关 topic
        preload_memory_max_topics=5,               # 一次最多加载的 topic 数
        preload_memory_max_chars=50_000,           # 预加载内容总字符上限
        preload_memory_candidate_limit=200,       # 筛选模型的候选 topic 数
    ),
)
```

`preload_memory_model` 不是 `AdvancedMemoryConfig` 字段，而是
`AdvancedMemorySessionService` 的可选参数，用于指定轻量筛选模型：

```python
session_service = AdvancedMemorySessionService(
    config=AdvancedMemoryConfig(preload_memory_enabled=True),
    preload_memory_model=small_model,  # 不传时复用主 Agent 的模型
)
```
