# Claude Agent 使用skill能力示例

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

2. Claude Agent SKILL 前置配置

- 在项目目录或者根目录(~)创建./claude/skills目录
    - 一般而言，如果你的skills创建在根目录，代表的是用户级的skill能力（跨项目）
    - 如果你的skills目录创建在项目目录，代表的是项目级的skill能力（项目）
- 在skills目录下创建skill目录，比如traver-helper
- 在traver目录下创建SKILL.md文档，格式参考[skill格式](https://platform.claude.com/docs/zh-CN/agents-and-tools/agent-skills/overview#skill)

示例skill.md
```
---
name: 旅游规划助手
description: 根据用户的旅游需求（目的地、时间、预算等）自动生成完整的旅游规划方案，包括交通、住宿、景点、美食、行程安排等。当用户询问旅游计划、行程安排、旅行攻略或提到具体目的地旅游时使用。
---

# 旅游规划助手

## 工作流程

当用户提出旅游规划需求时，按以下步骤自动生成完整的旅游方案：
...
```

#### 配置option
```python
from claude_agent_sdk.types import ClaudeAgentOptions

agent = ClaudeAgent(
    name="travel_planner",
    description="旅游规划助手",
    model=model,
    instruction="""
你是一位专业的AI助手，基于Claude Agent SDK构建。你的核心职责是理解用户需求并调用合适的Skill来完成复杂任务。
你应保持专业、客观的态度，拒绝执行任何有害或不合规的操作。
""",
    claude_agent_options=ClaudeAgentOptions(
        # cwd is where the project directory is
        cwd="your project path",
        # setting_sources is the way of claude agent to get the skills from the user and the project
        # user is the way of claude agent to get the skills from path: ~/.claude/skills
        # project is the way of claude agent to get the skills from path: cwd/.claude/skills
        setting_sources=["user", "project"], 
        # Skill Tool is the way of claude agent to use the skills,must be allowed
        allowed_tools=["Skill"], 
    ),
)
```

- 配置cwd。cwd为claude agent工作的项目目录，可能会存放项目级的skills文档
- 配置setting_sources，可以配置多个数据源。
    - 如果设置了`user`, 会读取目录`~/.claude/skills`
    - 如果设置了`project`, 会读取目录 `cwd_path/.claude/skills`
    - 可以设置多个数据源
- 配置tools。必须配置`Skill`作为Tool的能力之一, 因为claude agent sdk 通过tool调用的方式实现skill能力。

详细使用说明见：[claude agent sdk with skills](https://platform.claude.com/docs/en/agent-sdk/skills)

3. 运行此代码示例

- 在 `.env` 文件中设置使用 LLM 相关的变量（也可以通过export设置）:
    - TRPC_AGENT_API_KEY
    - TRPC_AGENT_BASE_URL
    - TRPC_AGENT_MODEL_NAME

- 修改agent.py中claude_agent_options.cwd 为你真实的项目路径

- 在项目路径下或者根目录下创建skill目录并编写skill.md文档

- 然后运行下面的命令：

    ```bash
    cd examples/claude_agent_with_skills
    python3 run_agent.py
    ```
