# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Prompts for agent."""

import platform
from pathlib import Path

from trpc_agent_sdk.log import logger

from ..config import BOT_NAME
from ..config import ClawConfig
from ..config import HISTORY_FILE_NAME
from ..config import MEMORY_FILE_NAME

INSTRUCTION_DEFAULT: str = "You are a helpful assistant."
SYSTEM_PROMPT_DEFAULT: str = f"You name is {BOT_NAME.replace('-', '_')}."
TOOL_AND_SKILL_FALLBACK: str = (
    "- If there is no direct tool that can answer/execute the request, first list available skills "
    "and select the relevant skill.\n"
    "- If a matching skill exists, call the skill directly instead of giving up or answering vaguely.\n"
    "- If a required skill is unavailable due to missing dependencies, clearly tell the user what is "
    "missing and suggest installing it first.\n"
    "- For skill shell execution, never fabricate command names. Use exact executable commands from "
    "loaded skill docs (for example, `curl ...` shown in SKILL.md).")

REPLY_POLICY = ("Reply directly with text for conversations. Only use the 'message' tool "
                "to send to a specific chat channel.")


class ClawPrompts:
    """Claw prompts."""

    def __init__(self, config: ClawConfig, silent: bool = False):
        """Initialize prompts."""
        self.workspace = config.workspace
        self.silent = silent
        self.config = config

    def _load_bootstrap_files(self) -> str:
        """Load all bootstrap files from workspace."""
        parts = []
        for filename in self.config.personal:
            file_path = Path(filename).resolve()
            if not file_path.exists():
                logger.warning("File %s not found", filename)
                continue
            filename = f"{file_path.stem.upper()}{file_path.suffix}"
            if filename in {HISTORY_FILE_NAME, MEMORY_FILE_NAME}:
                dest_file = self.workspace / "memory" / filename
            else:
                dest_file = self.workspace / filename
            content = file_path.read_text(encoding="utf-8")
            dest_file.write_text(content, encoding="utf-8")

            parts.append(f"## {filename}\n\n{content}")

        return "\n\n".join(parts) if parts else ""

    def build_system_prompt(self) -> str:
        """Build the system prompt from identity, bootstrap files, memory, and skills."""

        # 1. Identity
        parts = [self._get_identity()]

        # 2. Bootstrap files
        bootstrap = self._load_bootstrap_files()
        if bootstrap:
            parts.append(bootstrap)

        return "\n\n---\n\n".join(parts)

    def _get_identity(self) -> str:
        """Get the core identity section."""
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = (f"{'macOS' if system == 'Darwin' else system} {platform.machine()}"
                   f", Python {platform.python_version()}")
        instruction = self.config.agent.instruction or INSTRUCTION_DEFAULT
        system_prompt = self.config.agent.system_prompt or SYSTEM_PROMPT_DEFAULT
        long_term_memory = (f"- Long-term memory: {workspace_path}/memory/MEMORY.md (write important facts here)")
        history_log = (f"- History log: {workspace_path}/memory/HISTORY.md (grep-searchable). "
                       "Each entry starts with [YYYY-MM-DD HH:MM].")
        custom_skills = f"- Custom skills: {workspace_path}/skills/{{skill-name}}/SKILL.md"

        platform_policy = ""
        if system == "Windows":
            platform_policy = """## Platform Policy (Windows)
- You are running on Windows. Do not assume GNU tools like `grep`, `sed`, or `awk` exist.
- Prefer Windows-native commands or file tools when they are more reliable.
- If terminal output is garbled, retry with UTF-8 output enabled.
"""
        else:
            platform_policy = """## Platform Policy (POSIX)
- You are running on a POSIX system. Prefer UTF-8 and standard shell tools.
- Use file tools when they are simpler or more reliable than shell commands.
"""

        return f"""# {BOT_NAME}

{instruction}

{system_prompt}

## Runtime
{runtime}

## Workspace
Your workspace is at: {workspace_path}
{long_term_memory}
{history_log}
{custom_skills}

{platform_policy}

## {BOT_NAME} Guidelines
- State intent before tool calls, but NEVER predict or claim results before receiving them.
- Before modifying a file, read it first. Do not assume files or directories exist.
- After writing or editing a file, re-read it if accuracy matters.
- If a tool call fails, analyze the error before retrying with a different approach.
- Ask for clarification when the request is ambiguous.

{REPLY_POLICY}

## Tool and Skill Fallback
{TOOL_AND_SKILL_FALLBACK}
"""
