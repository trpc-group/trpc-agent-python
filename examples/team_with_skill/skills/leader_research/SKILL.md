---
name: leader-research
description: Generate concise research bullet points for the team leader.
---

Overview

Use this skill to create short, structured research notes before the
leader delegates tasks to team members.

Recommended Usage For This Demo

- Always run this skill first.
- Generate notes to `out/leader_notes.txt`, then delegate tasks to members.

Examples

1) Generate bullet points for a topic

   Command:

   bash scripts/gather_points.sh "renewable energy trends" out/leader_notes.txt

2) Generate combined notes for renewable energy and AI

   Command:

   bash scripts/gather_points.sh "renewable energy and AI trends in current year" out/leader_notes.txt

Output Files

- out/leader_notes.txt
