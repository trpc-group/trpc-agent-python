---
name: interactive-report
description: Generate a release report through an interactive command.
---

# Interactive release report

Use this skill to demonstrate `skill_exec` stdin handling and output
collection.

Run:

```bash
python3 scripts/create_report.py
```

The program asks for:

1. A release name.
2. Report mode `1` (compact) or `2` (detailed).

Use `skill_exec` with newline-separated initial `stdin` when the answers are
already known. Collect the generated file with:

```json
{
  "output_files": ["out/report.txt"],
  "save_as_artifacts": true,
  "artifact_prefix": "skill-exec-demo/"
}
```

Do not use `skill_run` for this demonstration.
