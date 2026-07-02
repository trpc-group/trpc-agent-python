# Dynamic Sub-Agent（动态子 Agent）

复杂任务往往需要委派子任务 —— 计算结果、搜索代码库、安全审计。直接在父 agent 的上下文中完成会带来几个问题：

- **上下文污染**：探索性搜索、工具输出、中间结果填满上下文窗口，把真正有用的信息挤走。
- **工具泛滥**：父 agent 一直携带所有工具，而大多数子任务只需其中一小部分。
- **角色无法隔离**：父 agent 只有一个 system prompt，无法为不同子任务切换不同人设或约束。
- **缺乏旁观视角**：自己写的代码很难自己发现问题。独立的上下文如同"第二双眼睛"，可以客观审计、质疑方案、验证结论，不受父 agent 推理路径的干扰。

**短期子 agent** 天然适合应对这些问题：每次委派都是独立上下文、只带需要的工具、有专属 system prompt。运行完返回结果即销毁，父 agent 始终保持干净聚焦。

**Dynamic Sub-Agent** 为父 agent 提供两种在运行时创建这类短期子 agent 的工具：

- **`SpawnSubAgentTool`** — 从**预定义目录**中选择标准化专家。instruction、工具集和模型在构造期由 archetype 锁定。

  适用于固定专家角色集合（安全审计员、代码探索者、方案规划者），让 LLM 按任务选择最合适的人选。父 LLM 通过 `subagent_type` 选择角色并写入任务 `prompt`，但无法修改子 agent 的 instruction 或工具。

- **`DynamicAgentTool`** — LLM **现场创造专家**，在调用时写入 instruction。无需预注册。

  适用于无法事先穷举所有专家类型的场景。每次调用都能定义不同角色 —— LLM 自行决定每次任务需要什么专长、约束和工具子集。

区别在于**谁定义角色**：开发者（Spawn）还是 LLM（Dynamic）。

## Quick Start

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.tools import SpawnSubAgentTool, DynamicAgentTool

# Spawn：从预定义目录中选择标准化专家
agent_with_spawn = LlmAgent(
    name="orchestrator",
    tools=[SpawnSubAgentTool()],  # 内置 `default` archetype
)

# Dynamic：LLM 现场定义专家角色
agent_with_dynamic = LlmAgent(
    name="orchestrator",
    tools=[DynamicAgentTool()],  # 子 agent 继承父 agent 全部工具
)
```

## 两种工具

| | `SpawnSubAgentTool` | `DynamicAgentTool` |
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

### `DynamicAgentTool`

LLM 在调用时写 instruction，现场创造任意专家。默认子 agent 继承父 agent 全部工具。

```python
class DynamicAgentTool(BaseTool):
    def __init__(
        self,
        name: str = "dynamic_agent",
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
| `name` | 工具名称。默认 `"dynamic_agent"`。 |
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
```

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

### DynamicAgentTool

**无边界（默认）**——子 agent 继承父 agent 全部工具，LLM 按需缩窄：

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.tools import DynamicAgentTool

orchestrator = LlmAgent(
    name="main",
    model=opus_model,
    instruction="当需要临时专家时，通过 dynamic_agent 创建子 agent。按需通过 tools 缩窄工具集。",
    tools=[DynamicAgentTool()],
)
```

**有边界**——子 agent 只能使用指定的工具集，父 agent 无法直接调用这些工具。适合将危险工具封装在子 agent 内部，父 agent 只能通过委派间接使用：

```python
orchestrator = LlmAgent(
    name="main",
    model=opus_model,
    instruction="你只能通过 dynamic_agent 调用工具，不要尝试直接调用。",
    tools=[
        DynamicAgentTool(
            tools=(calculator, word_count),
            expose_tool_selection=False,
        ),
    ],
)
```

## 补充说明

- **工具继承**：`DynamicAgentTool()` 默认子 agent 继承父 agent 全部工具；通过 `tools=(...)` 可限定子 agent 只能使用指定工具。`SpawnSubAgentTool` 的工具集由 archetype 决定（`tools=None` 时继承，否则使用 archetype 指定的工具）。无论哪种方式，spawn 工具始终从子 agent 中移除，防止递归。
- **会话隔离**：子 agent 在全新临时会话中运行，默认不共享父会话历史。通过 `include_parent_history=True` 可注入。
- **嵌套限制**：1 层硬限，子 agent 无法再次 spawn。
- **结果形态**：子 agent 的最终文本作为 tool result 字符串返回。
