# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Skill Processor implementation for TRPC Agent framework.

This module provides the SkillProcessor class which handles skill processing and execution.
"""

from __future__ import annotations

from typing import List

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.skills import BaseSkillRepository
from trpc_agent_sdk.skills import SKILL_DOCS_STATE_KEY_PREFIX
from trpc_agent_sdk.skills import SKILL_LOADED_STATE_KEY_PREFIX
from trpc_agent_sdk.skills import SKILL_TOOLS_STATE_KEY_PREFIX
from trpc_agent_sdk.skills import Skill
from trpc_agent_sdk.skills import generic_get_selection


class SkillsRequestProcessor:
    """
    Processor that injects skill overviews and loaded contents.

    Behavior:
      - Overview: injects names + descriptions (cheap).
      - Loaded skills: inject full SKILL.md body.
      - Docs: inject doc texts selected via state keys.

    State keys used (per turn, ephemeral):
      - skill.StateKeyLoadedPrefix+name -> "1"
      - skill.StateKeyDocsPrefix+name -> "*" or JSON array of file names.

    Attributes:
        repo: Skill repository
    """

    def __init__(self, skill_repository: BaseSkillRepository):
        """
        Create a new SkillsRequestProcessor.

        Args:
            repo: Skill repository
        """
        self._skill_repository = skill_repository

    async def process_llm_request(
        self,
        ctx: InvocationContext,
        request: LlmRequest,
    ) -> None:
        """
        Process a request by injecting skill information.

        Args:
            ctx: InvocationContext object
            request: LlmRequest object
            ctx: InvocationContext object
        """
        if request is None or ctx is None or self._skill_repository is None:
            logger.warning(
                "skills: process_llm_request failed: request is None or ctx is None or skill_repository is None")
            return

        self._inject_overview(request)

        loaded = self._get_loaded_skills(ctx)
        loaded.sort()

        parts = []
        for name in loaded:
            try:
                sk = self._skill_repository.get(name)
                if sk is None:
                    logger.warning("skills: get %s failed: skill not found", name)
                    continue
            except Exception as ex:  # pylint: disable=broad-except
                logger.warning("skills: get %s failed: %s", name, ex)
                continue

            if sk.body:
                parts.append(f"\n[Loaded] {name}\n\n{sk.body}\n")

            # Docs
            sel = self._get_docs_selection(ctx, name)
            # Summary line to make selected resources explicit.
            parts.append("Docs loaded: ")
            if not sel:
                parts.append("none\n")
            else:
                parts.append(", ".join(sel) + "\n")

            if sel:
                doc_text = self._build_docs_text(sk, sel)
                if doc_text:
                    parts.append(doc_text)

            # Tools
            tool_sel = self._get_tools_selection(ctx, name)
            # Summary line to make selected tools explicit
            parts.append("Tools selected: ")
            if not tool_sel:
                parts.append("none\n")
            else:
                parts.append(", ".join(tool_sel) + "\n")

        if parts:
            content = "".join(parts)
            self._merge_into_system(request, content)

        # Send a preprocessing trace event even when only overview is
        # injected, for consistent trace semantics.
        return loaded

    def _inject_overview(self, request: LlmRequest) -> None:
        """
        Inject skill overview into the request.

        Args:
            request: LlmRequest object
        """
        user_prompt = self._skill_repository.user_prompt()
        sums = self._skill_repository.summaries()
        if not sums:
            return
        skill_instructions = ""
        for s in sums:
            skill_instructions += f"- {s.name}: {s.description}\n"
        instruction = f"""
        Available skills:
        {skill_instructions}
        Tooling and workspace guidance:
        - Skills run inside an isolated workspace; you see only files that are in the workspace or have been staged there by tools.
        - Prefer $SKILLS_DIR, $WORK_DIR, $OUTPUT_DIR, $RUN_DIR, and $WORKSPACE_DIR over hard-coded paths when forming commands.
        - Treat $WORK_DIR/inputs (and a skill's inputs/ directory) as the place where tools stage user or host input files. Avoid overwriting or mutating these inputs directly.
        - When the user mentions external files, directories, artifacts, or URLs, decide whether to stage them into $WORK_DIR/inputs via available tools before reading.
        - Prefer writing new files under $OUTPUT_DIR or a skill's out/ directory and include output_files so files can be collected or saved as artifacts.
        - When chaining multiple skills, read previous results from $OUTPUT_DIR (or a skill's out/ directory) instead of copying them back into inputs directories.
        - If a skill is not loaded, call skill_load; you may pass docs or include_all_docs.
        - If the body is loaded but docs are missing, call skill_select_docs or call skill_load again to add docs.
        - If the skill defines tools in its SKILL.md, they will be automatically selected when you load the skill. You can refine tool selection with skill_select_tools.
        - When body and needed docs/tools are present, call skill_run to execute commands or use the skill's tools directly.
        - When using skill_run, do not invent command names. Copy executable commands from loaded SKILL.md examples or selected skill tools.
        """
        if user_prompt:
            instruction = f"{user_prompt}\n\n{instruction}"

        request.append_instructions([instruction])

    def _get_loaded_skills(self, ctx: InvocationContext) -> list[str]:
        """
        Get list of loaded skill names from session state.

        Args:
            ctx: InvocationContext object

        Returns:
            list of loaded skill names
        """
        names = []
        state = dict(ctx.session_state.copy())
        state.update(ctx.actions.state_delta)

        for k, v in state.items():
            if not k.startswith(SKILL_LOADED_STATE_KEY_PREFIX) or not v:
                continue
            name = k[len(SKILL_LOADED_STATE_KEY_PREFIX):]
            names.append(name)
        return names

    def _get_docs_selection(self, ctx: InvocationContext, name: str) -> list[str]:
        """
        Get the list of selected resources for a skill.

        Args:
            ctx: InvocationContext object
            name: Skill name

        Returns:
            list of selected document paths
        """

        def get_all_docs(skill_name: str) -> list[str]:
            """Get all doc paths for a skill."""
            try:
                sk = self._skill_repository.get(skill_name)
                if sk is None:
                    return []
                return [d.path for d in sk.resources]
            except Exception as ex:  # pylint: disable=broad-except
                logger.warning("Failed to get docs for skill '%s': %s", skill_name, ex)
                return []

        return generic_get_selection(ctx=ctx,
                                     skill_name=name,
                                     state_key_prefix=SKILL_DOCS_STATE_KEY_PREFIX,
                                     get_all_items_callback=get_all_docs)

    def _get_tools_selection(self, ctx: InvocationContext, name: str) -> list[str]:
        """
        Get the list of selected tools for a skill.

        Args:
            ctx: InvocationContext object
            name: Skill name

        Returns:
            list of selected tool names
        """

        def get_all_tools(skill_name: str) -> list[str]:
            """Get all tools for a skill."""
            try:
                sk = self._skill_repository.get(skill_name)
                if sk is None:
                    return []
                return sk.tools
            except Exception as ex:  # pylint: disable=broad-except
                logger.warning("Failed to get tools for skill '%s': %s", skill_name, ex)
                return []

        return generic_get_selection(ctx=ctx,
                                     skill_name=name,
                                     state_key_prefix=SKILL_TOOLS_STATE_KEY_PREFIX,
                                     get_all_items_callback=get_all_tools)

    def _build_docs_text(self, sk: Skill, wanted: List[str]) -> str:
        """
        Build documentation text for selected resources.

        Args:
            sk: Skill object
            wanted: List of wanted document paths

        Returns:
            Formatted documentation text
        """
        if sk is None or not sk.resources:
            return ""

        # Build a set for quick lookup of requested resources.
        want = set(wanted)

        parts = []
        for d in sk.resources:
            if d.path not in want:
                continue
            if not d.content:
                continue

            # Separate resources with a marker title.
            parts.append(f"\n[Doc] {d.path}\n\n{d.content}\n")

        return "".join(parts)

    def _merge_into_system(self, request: LlmRequest, content: str) -> None:
        """
        Merge content into the existing system message.

        Appends content into the existing system message when available;
        otherwise, it creates a new system message at the front.

        Args:
            request: LlmRequest object
            content: Content to merge
        """
        if request is None or not content:
            return
        request.append_instructions([content])
