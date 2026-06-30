# Dynamic Sub-Agent

Complex tasks often require delegated subtasks — computing results, searching codebases, auditing for security issues. Doing everything in the parent agent's own context causes several problems:

- **Context pollution**: exploratory searches, tool outputs, and intermediate steps fill the context window, crowding out what matters.
- **Tool sprawl**: the parent carries every tool all the time, even though most subtasks only need a subset.
- **No role isolation**: the parent has one system prompt; it cannot adopt a different persona or constraints per subtask.
- **No outside perspective**: an agent reviewing its own work is inherently biased — it's unlikely to spot its own mistakes. A fresh context acts as a second pair of eyes, auditing code, challenging a design, or verifying a claim without the parent's assumptions and reasoning shortcuts.

A **short-lived sub-agent** is a natural fit for these problems: a fresh context per delegation, only the tools it needs, a dedicated system prompt. It runs, returns its result, and is destroyed — keeping the parent conversation clean and focused.

**Dynamic Sub-Agent** gives the parent agent two tools for spawning these short-lived sub-agents at run time:

- **`SpawnSubAgentTool`** — choose from a **pre-defined catalog** of standardized specialists. Instruction, tool set, and model are locked by the archetype at construction time.

  Use this when you have a fixed set of expert roles (security auditor, code explorer, planner) and want the LLM to pick the right one per task. The parent LLM selects via `subagent_type` and writes a task-specific `prompt`, but cannot alter the sub-agent's instruction or tools.

- **`DynamicAgentTool`** — the LLM **invents the specialist on the fly**, writing the instruction at call time. No pre-registration needed.

  Use this when you cannot predict all the specialist types you'll need ahead of time. Every call can define a different role — the LLM decides what expertise, constraints, and tool subset each task requires.

The difference is *who defines the role*: the developer (Spawn) or the LLM (Dynamic).

## Quick Start

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.tools import SpawnSubAgentTool, DynamicAgentTool

# Spawn: pick from a catalog of pre-defined specialists
agent_with_spawn = LlmAgent(
    name="orchestrator",
    tools=[SpawnSubAgentTool()],  # built-in `default` archetype
)

# Dynamic: LLM writes the specialist's role at call time
agent_with_dynamic = LlmAgent(
    name="orchestrator",
    tools=[DynamicAgentTool()],  # sub-agent inherits all parent tools
)
```

## Two Tools

| | `SpawnSubAgentTool` | `DynamicAgentTool` |
| --- | --- | --- |
| **Pattern** | Pick from a pre-defined catalog | LLM invents role at call time |
| **Who defines the role** | Developer, two modes:<br>① `SubAgentArchetype` in code<br>② Markdown file (YAML frontmatter + body) | LLM (via `instruction` parameter) |
| **Best for** | Standardized, repeatable specialists | Roles you can't pre-register |
| **Role flexibility** | Locked — only `prompt` varies | Full — every call can be different |
| **Tool surface** | Locked by archetype | Inherits parent tools; LLM can narrow via `tools` |

### `SpawnSubAgentTool`

Dispatches tasks to pre-registered archetypes. The parent LLM picks the right specialist via `subagent_type`; its instruction and tools are fixed.

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

| parameter | meaning |
| --- | --- |
| `agents` | Additional archetypes to register. |
| `agent_paths` | Directories of `*.md` files to load archetypes from disk. |
| `tool_mapping` | Custom tool name → tool class mapping for resolving MD frontmatter. |
| `with_default` | Whether to register the built-in `default` archetype. Default `True`. |
| `agent_config` | `SubAgentConfig` applied to every spawned sub-agent. |
| `skip_summarization` | When `True`, skip the parent's summarization turn after the sub-agent returns. |

**Three ways to configure:**

```python
# Zero config — only the built-in `default` archetype
SpawnSubAgentTool()

# Code-defined archetypes
SpawnSubAgentTool(agents=[security_auditor, EXPLORE_AGENT, PLAN_AGENT])

# Load from Markdown files
SpawnSubAgentTool(agent_paths=[".trpc_agents/"])
```

#### `SubAgentArchetype`

A frozen template that describes *one kind of sub-agent the parent is allowed to spawn*. It locks down the dangerous knobs (instruction, tools, model) so prompt-injected calls cannot reshape the sub-agent.

```python
@dataclass(frozen=True)
class SubAgentArchetype:
    name: str                      # registry key + the value LLM passes as `subagent_type`
    description: str               # what the LLM reads to pick this archetype
    instruction: str | InstructionProvider
    tools: tuple | None = None     # None = inherit all parent tools
    model: Any = None              # None = inherit via SubAgentConfig or parent's model
```

- **`description`** — read by the **parent LLM** when selecting which archetype to spawn. Third-person, selection-focused.
- **`instruction`** — the **sub-agent's** system prompt. Second-person, execution-focused. Supports both strings and `InstructionProvider` callables.

#### Built-in Archetypes

| name | tools | typical use |
| --- | --- | --- |
| `default` | `None` (inherits all parent tools) | **Neutral task executor.** Does not impose a specific role. **Auto-registered.** |
| `general-purpose` | `None` (inherits all parent tools) | **Researcher / explorer** with soft "NEVER create files" constraints. Opt-in only. |
| `Explore` | `Read` / `Glob` / `Grep` / `WebFetch` | Read-only search: locate files, grep symbols. |
| `Plan` | `Read` / `Glob` / `Grep` | Design implementation plans without modifying code. |

Only `default` is auto-registered. `general-purpose`, `Explore`, and `Plan` must be explicitly added via the `agents` parameter.

### `DynamicAgentTool`

The LLM writes the sub-agent's `instruction` at call time, creating any specialist on the fly. By default the sub-agent inherits all parent tools.

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

| parameter | meaning |
| --- | --- |
| `name` | Tool name. Default `"dynamic_agent"`. |
| `description` | Tool description. |
| `tools` | Fixed tool set for the sub-agent. `None` (default) = inherit all parent tools. |
| `expose_tool_selection` | When `True` (default), the `tools` field is exposed so the LLM can narrow the tool surface per call. |
| `agent_config` | `SubAgentConfig` applied to every spawned sub-agent. |
| `skip_summarization` | When `True`, skip the parent's summarization turn after the sub-agent returns. |

## Shared Configuration

### `SubAgentConfig`

Unified construction-time defaults for every spawned sub-agent. `None` means "inherit from the parent agent".

```python
@dataclass(frozen=True)
class SubAgentConfig:
    model: LLMModel | None = None
    """Model for the sub-agent. None inherits the parent's model."""

    generate_content_config: GenerateContentConfig | None = None
    """Generation config (temperature, top_p, etc.). None inherits from parent."""

    parallel_tool_calls: bool | None = None
    """Whether the sub-agent may issue parallel tool calls. None inherits from parent."""

    include_parent_history: bool = False
    """Whether to inject parent conversation history into the sub-agent's session."""

    max_parent_history_turns: int | None = None
    """Max parent turns to inject. None = unlimited. Only used when include_parent_history=True."""

    max_turns: int | None = None
    """Max LLM calls the sub-agent may make. None = unlimited."""
```

## Usage

### SpawnSubAgentTool

**Zero config** — only the built-in `default` archetype:

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.tools import SpawnSubAgentTool

orchestrator = LlmAgent(
    name="main",
    model=opus_model,
    instruction="When a task benefits from isolated context, spawn a sub-agent via spawn_subagent.",
    tools=[SpawnSubAgentTool()],
)
```

**Code-defined archetypes**:

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

**Loading archetypes from Markdown files**:

Place `.md` files in a directory with YAML frontmatter:

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

**Unbounded (default)** — the sub-agent inherits all parent tools. The LLM narrows the tool set per call via `tools`:

```python
from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.tools import DynamicAgentTool

orchestrator = LlmAgent(
    name="main",
    model=opus_model,
    instruction="When you need a specialist, create one via dynamic_agent. Narrow tools as needed.",
    tools=[DynamicAgentTool()],
)
```

**Bounded** — the sub-agent uses a fixed tool set. The parent agent has no direct access to those tools; every task must be delegated. This is useful for keeping dangerous tools behind the sub-agent boundary:

```python
orchestrator = LlmAgent(
    name="main",
    model=opus_model,
    instruction="You can only use tools by delegating via dynamic_agent. Do not attempt direct calls.",
    tools=[
        DynamicAgentTool(
            tools=(calculator, word_count),
            expose_tool_selection=False,
        ),
    ],
)
```

## Additional Notes

- **Tool inheritance**: `DynamicAgentTool()` inherits all parent tools by default; pass `tools=(...)` to give the sub-agent a fixed set instead. For `SpawnSubAgentTool`, the archetype's `tools` field decides — `None` means inherit, `(ReadTool, ...)` means that exact set. In all cases, spawn tools are stripped from the sub-agent to prevent recursion.
- **Session isolation**: sub-agents run in a fresh ephemeral session. Parent history is not shared by default; opt in via `include_parent_history=True`.
- **Nesting**: 1-level hard cap. Sub-agents cannot spawn further sub-agents.
- **Result shape**: the sub-agent's final text is returned as the tool result string.
