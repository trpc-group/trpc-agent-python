# TeamAgent With Skill Example

This example demonstrates TeamAgent coordinate mode where the **leader**
can use Agent Skills to extend context before delegating to members.

## Features

- Leader + Researcher + Writer team structure
- Leader can call skill tools (`skill_list`, `skill_load`, `skill_run`, etc.)
- Members still focus on specialized tasks
- Single-turn conversation with mandatory skill-first workflow

## Project Structure

```text
examples/team_with_skill/
├── agent/
│   ├── agent.py
│   ├── config.py
│   ├── prompts.py
│   ├── tools.py
│   └── __init__.py
├── skills/
│   └── leader_research/
│       ├── SKILL.md
│       └── scripts/gather_points.sh
├── run_agent.py
└── README.md
```

## Requirements

- Python 3.10+
- Environment variables:
  - `TRPC_AGENT_API_KEY`
  - `TRPC_AGENT_BASE_URL`
  - `TRPC_AGENT_MODEL_NAME`
- Optional:
  - `SKILLS_ROOT` (defaults to `./skills` under this example)

## Run

```bash
cd examples/team_with_skill
python3 run_agent.py
```

## Expected Behavior

For the demo query:

1. Leader calls skill tools first to gather extra context
2. Leader delegates once to `researcher`
3. Leader delegates once to `writer`
4. Leader returns final response
