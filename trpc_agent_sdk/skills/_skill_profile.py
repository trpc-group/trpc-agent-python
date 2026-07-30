# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Skill tool profile options and flags."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ._constants import SKILL_TOOLS_NAMES
from ._constants import SkillProfileNames
from ._constants import SkillToolsNames


@dataclass
class SkillProfileFlags:
    """Built-in skill tool flags"""
    load: bool = False
    select_docs: bool = False
    list_docs: bool = False
    run: bool = False
    select_tools: bool = False
    list_skills: bool = False
    exec: bool = False
    write_stdin: bool = False
    poll_session: bool = False
    kill_session: bool = False

    @classmethod
    def normalize_profile(cls, profile: str) -> str:
        p = (profile or "").strip().lower()
        if p == str(SkillProfileNames.KNOWLEDGE_ONLY):
            return str(SkillProfileNames.KNOWLEDGE_ONLY)
        return str(SkillProfileNames.FULL)

    @classmethod
    def normalize_tool(cls, name: str) -> str:
        return (name or "").strip().lower()

    @classmethod
    def preset_flags(cls, profile: str, forbidden_tools: Optional[list[str]] = None) -> "SkillProfileFlags":
        normalized = cls.normalize_profile(profile)
        if normalized == SkillProfileNames.KNOWLEDGE_ONLY:
            return cls(load=True, select_docs=True, list_docs=True)

        flags = cls(
            load=True,
            select_docs=True,
            list_docs=True,
            run=True,
            exec=True,
            write_stdin=True,
            poll_session=True,
            kill_session=True,
            select_tools=True,
            list_skills=True,
        )
        if forbidden_tools:
            flags = flags.flags_from_forbidden_tools(forbidden_tools, flags)
        return flags

    @classmethod
    def flags_from_forbidden_tools(cls, forbidden_tools: list[str], flags: "SkillProfileFlags") -> "SkillProfileFlags":
        for raw in forbidden_tools:
            name = cls.normalize_tool(raw)
            if name in SKILL_TOOLS_NAMES:
                flags_mem = name[name.index("_") + 1:]
                setattr(flags, flags_mem, False)
        flags.validate()
        return flags

    @classmethod
    def resolve_flags(cls, profile: str, forbidden_tools: Optional[list[str]] = None) -> "SkillProfileFlags":
        flags = cls.preset_flags(profile, forbidden_tools)
        flags.validate()
        return flags

    def validate(self) -> None:
        if self.exec and not self.run:
            raise ValueError(f"{SkillToolsNames.EXEC} requires {SkillToolsNames.RUN}")
        if self.write_stdin and not self.exec:
            raise ValueError(f"{SkillToolsNames.WRITE_STDIN} requires {SkillToolsNames.EXEC}")
        if self.poll_session and not self.exec:
            raise ValueError(f"{SkillToolsNames.POLL_SESSION} requires {SkillToolsNames.EXEC}")
        if self.kill_session and not self.exec:
            raise ValueError(f"{SkillToolsNames.KILL_SESSION} requires {SkillToolsNames.EXEC}")

    def is_any(self) -> bool:
        return any((
            self.load,
            self.select_docs,
            self.list_docs,
            self.run,
            self.exec,
            self.write_stdin,
            self.poll_session,
            self.kill_session,
            self.select_tools,
            self.list_skills,
        ))

    def has_knowledge_tools(self) -> bool:
        return self.load or self.select_docs or self.list_docs

    def has_doc_helpers(self) -> bool:
        return self.select_docs or self.list_docs

    def has_select_tools(self) -> bool:
        return self.select_tools

    def requires_execution_tools(self) -> bool:
        return self.run or self.exec or self.write_stdin or self.poll_session or self.kill_session

    def requires_exec_session_tools(self) -> bool:
        return self.exec or self.write_stdin or self.poll_session or self.kill_session

    def without_interactive_execution(self) -> "SkillProfileFlags":
        return SkillProfileFlags(
            load=self.load,
            select_docs=self.select_docs,
            list_docs=self.list_docs,
            run=self.run,
            exec=False,
            write_stdin=False,
            poll_session=False,
            kill_session=False,
        )
