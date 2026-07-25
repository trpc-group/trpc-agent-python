---
name: safety_demo
description: Executes commands through skill_run for Tool Script Safety Guard demo.
---

# Safety Demo Skill

When asked to run a command, use `skill_run` with:

- `skill`: `safety_demo`
- `command`: the exact command from the user

The safety filter attached to `skill_run` scans the command before the skill
workspace executes it.
