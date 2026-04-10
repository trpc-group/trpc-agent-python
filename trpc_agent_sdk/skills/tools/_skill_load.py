# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Load a skill body and optional docs. Safe to call multiple times to add or replace docs.
Do not call this to list skills; names and descriptions are already in context.
Use when a task needs a skill's SKILL.md body and selected docs in context.
"""

from __future__ import annotations

import json
from typing import Any
from typing import List
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type

from .._common import append_loaded_order_state_delta
from .._common import docs_state_key
from .._common import loaded_state_key
from .._common import set_state_delta
from .._common import tool_state_key
from .._repository import BaseSkillRepository
from .._types import Skill
from ..stager import SkillStageRequest
from ..stager import Stager
from ._common import CreateWorkspaceNameCallback
from ._common import default_create_ws_name_callback
from ._common import set_staged_workspace_dir
from ._copy_stager import CopySkillStager


class SkillLoadTool(BaseTool):
    """Tool for loading a skill."""

    def __init__(
        self,
        repository: BaseSkillRepository,
        skill_stager: Optional[Stager] = None,
        create_ws_name_cb: Optional[CreateWorkspaceNameCallback] = None,
        filters: Optional[List[BaseFilter]] = None,
    ):
        super().__init__(name="skill_load", description="Load a skill.", filters=filters)
        self._repository = repository
        self._skill_stager: Stager = skill_stager or CopySkillStager()
        self._create_ws_name_cb: Optional[
            CreateWorkspaceNameCallback] = create_ws_name_cb or default_create_ws_name_callback

    @override
    def _get_declaration(self) -> Optional[FunctionDeclaration]:
        return FunctionDeclaration(
            name="skill_load",
            description=("Load a skill body and optional docs. Safe to call multiple times to add or replace docs. "
                         "Do not call this to list skills; names and descriptions are already in context. "
                         "Use when a task needs a skill's SKILL.md body and selected docs in context."),
            parameters=Schema(
                type=Type.OBJECT,
                required=["skill_name"],
                properties={
                    "skill_name":
                    Schema(type=Type.STRING, description="The name of the skill to load."),
                    "docs":
                    Schema(type=Type.ARRAY,
                           default=None,
                           items=Schema(type=Type.STRING),
                           description="The docs of the skill to load."),
                    "include_all_docs":
                    Schema(type=Type.BOOLEAN, default=False, description="Whether to include all docs of the skill."),
                },
            ),
            response=Schema(type=Type.STRING,
                            description="Result of skill_load. message is a string indicating the skill was loaded."),
        )

    @override
    async def _run_async_impl(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> str:
        if not (args["skill_name"] or "").strip():
            raise ValueError("skill_name is required")
        skill_name = args["skill_name"]
        docs = args.get("docs", [])
        include_all_docs = args.get("include_all_docs", False)
        normalized_skill = skill_name.strip()
        skill = self._repository.get(normalized_skill)
        await self._ensure_staged(ctx=tool_context, skill_name=skill_name)
        clean_docs = [doc.strip() for doc in (docs or []) if isinstance(doc, str) and doc.strip()]
        self.__set_state_delta_for_skill_load(tool_context, skill_name, clean_docs, include_all_docs)
        if skill.tools:
            self.__set_state_delta_for_skill_tools(tool_context, skill)
        return f"skill {skill_name!r} loaded"

    async def _ensure_staged(self, *, ctx: InvocationContext, skill_name: str) -> None:
        runtime = self._repository.workspace_runtime
        manager = runtime.manager(ctx)
        ws_id = self._create_ws_name_cb(ctx)
        ws = await manager.create_workspace(ws_id, ctx)
        result = await self._skill_stager.stage_skill(
            SkillStageRequest(skill_name=skill_name, repository=self._repository, workspace=ws, ctx=ctx))
        set_staged_workspace_dir(ctx, skill_name, result.workspace_skill_dir)

    def __set_state_delta_for_skill_load(self,
                                         invocation_context: InvocationContext,
                                         skill_name: str,
                                         docs: list[str],
                                         include_all_docs: bool = False) -> None:
        """Set state delta for skill_load, aligned with Go StateDeltaForInvocation."""
        agent_name = invocation_context.agent_name.strip()
        delta, normalized_skill = self.__build_state_delta_for_skill_load(
            invocation_context=invocation_context,
            skill_name=skill_name,
            docs=docs,
            include_all_docs=include_all_docs,
        )
        invocation_context.actions.state_delta.update(delta)
        append_loaded_order_state_delta(invocation_context, agent_name, normalized_skill)

    def __build_state_delta_for_skill_load(
        self,
        invocation_context: InvocationContext,
        skill_name: str,
        docs: list[str],
        include_all_docs: bool = False,
    ) -> tuple[dict[str, Any], str]:
        """Build skill_load state delta."""
        normalized_skill = skill_name.strip()
        if not normalized_skill:
            return {}, ""
        delta: dict[str, Any] = {}
        delta[loaded_state_key(invocation_context, normalized_skill)] = True
        if include_all_docs:
            delta[docs_state_key(invocation_context, normalized_skill)] = '*'
        else:
            delta[docs_state_key(invocation_context, normalized_skill)] = json.dumps(docs or [])
        return delta, normalized_skill

    def __set_state_delta_for_skill_tools(self, invocation_context: InvocationContext, skill: Skill) -> None:
        """Set the state delta of a skill tools."""
        normalized_skill = skill.summary.name.strip()
        if not normalized_skill:
            return
        key = tool_state_key(invocation_context, normalized_skill)
        set_state_delta(invocation_context, key, json.dumps(skill.tools))
