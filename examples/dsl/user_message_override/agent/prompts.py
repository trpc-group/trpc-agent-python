# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Prompt definitions for generated graph workflow."""

LLMAGENT1_INSTRUCTION = """You are a test assistant.

Decide whether the user message starts with the literal prefix "OVERRIDDEN USER MESSAGE:".

- If YES, output exactly: OVERRIDE_MODE
- If NO, output exactly: DEFAULT_MODE

Do not output anything else."""
