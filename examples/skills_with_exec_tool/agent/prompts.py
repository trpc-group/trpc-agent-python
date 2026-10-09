# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Instructions for the skill execution example."""

INSTRUCTION = """
You demonstrate interactive Agent Skill execution.

There is one skill: interactive-report.
Always call skill_load before executing it.
Use skill_exec, never skill_run, for the demonstration.
Pass the requested stdin, output_files, save_as_artifacts, and artifact_prefix
arguments unchanged. After execution, report:

- process status and exit code
- collected output_files, including report content and workspace ref
- persisted artifact_files

Do not replace the interactive script with shell redirection or another
command.
"""
