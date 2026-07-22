---
name: safety_demo
description: Demonstrates command execution through skill_run for Tool Script Safety Guard.
---

# Safety Demo Skill

This skill is used only by `examples/tool_safety/real_agent_demo`.

When asked to run a safety scenario, call `skill_run` with:

- `skill`: `safety_demo`
- `command`: the exact command requested by the user
- `timeout`: `10`

The safety filter attached to `skill_run` scans the command before the skill
workspace executes it.

