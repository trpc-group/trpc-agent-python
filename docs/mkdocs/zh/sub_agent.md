# Spawned Sub-Agents

复杂任务往往需要委派子任务 —— 计算结果、搜索代码库、安全审计。直接在父 agent 的上下文中完成会带来几个问题：

- **上下文污染**：探索性搜索、工具输出、中间结果填满上下文窗口，把真正有用的信息挤走。
- **工具泛滥**：父 agent 一直携带所有工具，而大多数子任务只需其中一小部分。
- **角色无法隔离**：父 agent 只有一个 system prompt，无法为不同子任务切换不同人设或约束。
- **缺乏旁观视角**：自己写的代码很难自己发现问题。独立的上下文如同"第二双眼睛"，可以客观审计、质疑方案、验证结论，不受父 agent 推理路径的干扰。

**短期子 agent** 天然适合应对这些问题：每次委派都是独立上下文、只带需要的工具、有专属 system prompt。运行完返回结果即销毁，父 agent 始终保持干净聚焦。

**Spawned Sub-Agents** 为父 agent 提供两种在运行时创建短期子 agent 的工具：

- **`SpawnSubAgentTool`** — 从**预定义目录**中选择标准化专家。instruction、工具集和模型在构造期由 archetype 锁定。

  适用于固定专家角色集合（安全审计员、代码探索者、方案规划者），让 LLM 按任务选择最合适的人选。父 LLM 通过 `subagent_type` 选择角色并写入任务 `prompt`，但无法修改子 agent 的 instruction 或工具。

- **`DynamicSubAgentTool`** — LLM **现场创造专家**，在调用时写入 instruction。无需预注册。

  适用于无法事先穷举所有专家类型的场景。每次调用都能定义不同角色 —— LLM 自行决定每次任务需要什么专长、约束和工具子集。

区别在于**谁定义角色**：开发者（Spawn）还是 LLM（Dynamic）。

### 与框架其他多 agent 机制的区别

框架已有几种组合 agent 的方式（详见 [Multi Agents](multi_agents.md)）。Spawned Sub-Agents 与它们解决的是不同问题：

| 机制 | 参与的 agent | 谁决定何时调用 | 上下文 | 典型用途 |
| --- | --- | --- | --- | --- |
| **Chain / Parallel / Cycle Agent** | **预先构建**的固定 agent 实例 | **确定性**编排——按列表顺序/并行/循环执行，与输入无关 | 各 agent 独立 | 固定的多步工作流 |
| **Sub Agents（transfer）** | 预先注册的 agent | 父 agent 运行时**转移控制权**，之后由子 agent 接管对话 | 共享同一会话 | 把整段对话**移交**给更合适的 agent |
| **AgentTool** | 把**某个已有 agent 实例**包成工具 | 父 LLM 按需调用 | 会共享/同步 state 与 artifact 回父 | 复用一个**具体的、已存在的** agent |
| **Spawned Sub-Agents** | **调用时临时创建**、用完即销毁 | 父 LLM 按需调用 | **严格隔离**：全新临时会话，默认不共享历史/state | 委派**一次性**子任务，保持父上下文干净 |

一句话概括：**Chain/Parallel/Cycle** 是"确定性编排一组固定 agent"；**transfer** 是"把对话交出去"；**AgentTool** 是"把一个已有 agent 当工具复用"；而 **Spawned Sub-Agents** 是"为单次任务**现场造一个隔离的、短命的**子 agent，跑完就丢"——强调的是**运行时按需创建**与**上下文隔离**，而非复用既有 agent 或转移控制。

## Quick Start

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.tools import SpawnSubAgentTool, DynamicSubAgentTool

# Spawn：从预定义目录中选择标准化专家
agent_with_spawn = LlmAgent(
    name="orchestrator",
    tools=[SpawnSubAgentTool()],  # 内置 `default` archetype
)

# Dynamic：LLM 现场定义专家角色
agent_with_dynamic = LlmAgent(
    name="orchestrator",
    tools=[DynamicSubAgentTool()],  # 子 agent 继承父 agent 全部工具
)
```

## 两种工具

| | `SpawnSubAgentTool` | `DynamicSubAgentTool` |
| --- | --- | --- |
| **模式** | 从预定义目录中选择 | LLM 现场写 instruction |
| **谁定义角色** | 开发者，支持两种方式：<br>① 代码构造 `SubAgentArchetype`<br>② Markdown 文件（YAML 头 + body） | LLM（通过 `instruction` 参数） |
| **适用场景** | 标准化、可复用的专家 | 无法预注册的临时角色 |
| **角色灵活性** | 锁定 —— 仅 `prompt` 可变 | 完全灵活 —— 每次调用可不同 |
| **工具面** | 由 archetype 锁定 | 继承父工具；LLM 可通过 `tools` 缩窄 |

### `SpawnSubAgentTool`

从预注册 archetype 目录派发任务。父 LLM 通过 `subagent_type` 选择合适的专家；instruction 和工具集由 archetype 锁定。

```python
class SpawnSubAgentTool(BaseTool):
    def __init__(
        self,
        agents: list[SubAgentArchetype] | None = None,
        agent_paths: list[str | os.PathLike] | None = None,
        tool_mapping: dict[str, Any] | None = None,
        with_default: bool = True,
        agent_config: SubAgentConfig | None = None,
        skip_summarization: bool = False,
        filters_name: list[str] | None = None,
        filters: list[BaseFilter] | None = None,
    ) -> None: ...
```

| 参数 | 含义 |
| --- | --- |
| `agents` | 额外注册的 archetype 列表。 |
| `agent_paths` | 包含 `*.md` 文件的目录，从磁盘加载 archetype。 |
| `tool_mapping` | 自定义工具名到工具类的映射，用于解析 MD 文件中的工具名。 |
| `with_default` | 是否注册内置 `default` archetype。默认 `True`。 |
| `agent_config` | 应用于每个子 agent 的 `SubAgentConfig`。 |
| `skip_summarization` | 为 `True` 时，子 agent 返回后跳过父 agent 的总结回合。 |

**三种接入方式：**

```python
# 零配置 —— 仅内置 `default` archetype
SpawnSubAgentTool()

# 代码定义 archetype
SpawnSubAgentTool(agents=[security_auditor, EXPLORE_AGENT, PLAN_AGENT])

# 从 Markdown 文件加载
SpawnSubAgentTool(agent_paths=[".trpc_agents/"])
```

#### `SubAgentArchetype`（子 agent 原型）

一个不可变模板，描述**父 agent 被允许创建的某一种子 agent**。将 instruction / tools / model 锁定，防止被 prompt 注入越权改写。

```python
@dataclass(frozen=True)
class SubAgentArchetype:
    name: str                      # registry key，也是 LLM 传入的 `subagent_type` 值
    description: str               # 父 LLM 选择时读到的判断标准
    instruction: str | InstructionProvider
    tools: tuple | None = None     # None = 继承父 agent 全部工具
    model: Any = None              # None = 通过 SubAgentConfig 或继承父 agent 模型
```

- **`description`** — 父 LLM 在选择 archetype 时读到，第三人称、面向选择决策。
- **`instruction`** — 子 agent 的 system prompt，第二人称、面向执行。支持字符串或 `InstructionProvider` 可调用对象。

#### 内置 Archetype

| name | tools | 典型用途 |
| --- | --- | --- |
| `default` | `None`（继承父 agent 全部工具） | **中性任务执行者**。不塑造特定人格。**默认注册。** |
| `general-purpose` | `None`（继承父 agent 全部工具） | **研究员人格**，带"NEVER create files"等软约束。需手动注册。 |
| `Explore` | `Read` / `Glob` / `Grep` / `WebFetch` | 只读搜索：定位文件、grep 符号。 |
| `Plan` | `Read` / `Glob` / `Grep` | 设计实现方案，不修改代码。 |

仅 `default` 默认注册。`general-purpose` / `Explore` / `Plan` 需手动通过 `agents` 参数注册。

### `DynamicSubAgentTool`

LLM 在调用时写 instruction，现场创造任意专家。默认子 agent 继承父 agent 全部工具。

```python
class DynamicSubAgentTool(BaseTool):
    def __init__(
        self,
        name: str = "dynamic_subagent",
        description: str | None = None,
        tools: tuple | None = None,
        expose_tool_selection: bool = True,
        agent_config: SubAgentConfig | None = None,
        skip_summarization: bool = False,
        filters_name: list[str] | None = None,
        filters: list[BaseFilter] | None = None,
    ) -> None: ...
```

| 参数 | 含义 |
| --- | --- |
| `name` | 工具名称。默认 `"dynamic_subagent"`。 |
| `description` | 工具描述。 |
| `tools` | 子 agent 的固定工具集。`None`（默认）= 继承父 agent 全部工具。 |
| `expose_tool_selection` | 为 `True`（默认）时暴露 `tools` 字段，LLM 可按需缩窄工具面。 |
| `agent_config` | 应用于每个子 agent 的 `SubAgentConfig`。 |
| `skip_summarization` | 为 `True` 时，子 agent 返回后跳过父 agent 的总结回合。 |

## 共享配置

### `SubAgentConfig`

每个子 agent 的统一构造期默认值。`None` 表示继承父 agent 的对应配置。

```python
@dataclass(frozen=True)
class SubAgentConfig:
    model: LLMModel | None = None
    """子 agent 使用的模型。None 继承父 agent 模型。"""

    generate_content_config: GenerateContentConfig | None = None
    """生成配置（temperature、top_p 等）。None 继承父 agent 配置。"""

    parallel_tool_calls: bool | None = None
    """子 agent 是否可并行调用工具。None 继承父 agent 配置。"""

    include_parent_history: bool = False
    """是否将父 agent 的会话历史注入子 agent。"""

    max_parent_history_turns: int | None = None
    """注入的最大父会话轮数。None = 不限制。仅在 include_parent_history=True 时生效。"""

    max_turns: int | None = None
    """子 agent 最多可发起的 LLM 调用次数。None = 不限制。"""

    forward_events: bool = False
    """是否将子 agent 的执行事件转发给父 runner 的消费者，作为进度更新。

    True：编排层可实时展示子 agent 的执行（模型输出、工具调用、工具结果），
    父 agent 的 LLM 仍只收到子 agent 的最终结果。
    False（默认）：子 agent 静默执行，只回传最终结果。"""
```

转发的事件以进度事件形式到达消费者，**不会**写入父会话、也**不会**进入父 agent 的 LLM 上下文；消费者通过 `event.custom_metadata` 上的 `tool_progress=True` 识别它们，并从 `payload` 读取执行内容（`author` / `partial` / `content` / 可选 `error` / `usage`）。

## 使用方式

### SpawnSubAgentTool

**零配置**——仅内置 `default` archetype：

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.tools import SpawnSubAgentTool

orchestrator = LlmAgent(
    name="main",
    model=opus_model,
    instruction="当任务适合在隔离上下文中处理时，通过 spawn_subagent 创建子 agent。",
    tools=[SpawnSubAgentTool()],
)
```

**代码定义 Archetype**：

```python
from trpc_agent_sdk.agents.sub_agent import SubAgentArchetype
from trpc_agent_sdk.tools import SpawnSubAgentTool

security_auditor = SubAgentArchetype(
    name="security-auditor",
    description="Use for security code audit. **IMPORTANT:** This agent is read-only.",
    instruction="You are a security auditor...",
    tools=(ReadTool, GrepTool, GlobTool),
)

orchestrator = LlmAgent(
    tools=[SpawnSubAgentTool(agents=[security_auditor])],
)
```

**从 Markdown 文件加载 Archetype**：

在目录下放置 `.md` 文件，YAML 前置元数据声明 name / description 和可选 tools：

```markdown
---
name: security-auditor
description: Use for security code audit.
tools:
  - Read
  - Glob
  - Grep
---

You are a security auditor...
```

```python
tools=[SpawnSubAgentTool(agent_paths=[".trpc_agents/"])]
```

### DynamicSubAgentTool

**无边界（默认）**——子 agent 继承父 agent 全部工具，LLM 按需缩窄：

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.tools import DynamicSubAgentTool

orchestrator = LlmAgent(
    name="main",
    model=opus_model,
    instruction="当需要临时专家时，通过 dynamic_subagent 创建子 agent。按需通过 tools 缩窄工具集。",
    tools=[DynamicSubAgentTool()],
)
```

**有边界**——子 agent 只能使用指定的工具集，父 agent 无法直接调用这些工具。适合将危险工具封装在子 agent 内部，父 agent 只能通过委派间接使用：

```python
orchestrator = LlmAgent(
    name="main",
    model=opus_model,
    instruction="你只能通过 dynamic_subagent 调用工具，不要尝试直接调用。",
    tools=[
        DynamicSubAgentTool(
            tools=(calculator, word_count),
            expose_tool_selection=False,
        ),
    ],
)
```

## 补充说明

- **工具继承**：`DynamicSubAgentTool()` 默认子 agent 继承父 agent 全部工具；通过 `tools=(...)` 可限定子 agent 只能使用指定工具。`SpawnSubAgentTool` 的工具集由 archetype 决定（`tools=None` 时继承，否则使用 archetype 指定的工具）。无论哪种方式，spawn 工具始终从子 agent 中移除，防止递归。
- **会话隔离**：子 agent 在全新临时会话中运行，默认不共享父会话历史。通过 `include_parent_history=True` 可注入。
- **嵌套限制**：1 层硬限，子 agent 无法再次 spawn。
- **结果形态**：子 agent 的最终文本作为 tool result 字符串返回。
- **实时执行（`forward_events`）**：设置 `SubAgentConfig(forward_events=True)` 可将子 agent 的执行流转发给父 runner 的消费者用于展示。转发事件为进度事件——它们不会进入父 agent 的 LLM 上下文（父仍只收到最终结果）。消费者通过 `event.custom_metadata` 上的 `tool_progress=True` 识别它们并读取 `payload`。可运行示例见 `examples/dynamic_subagent` 与 `examples/spawn_subagent`。
