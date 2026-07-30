# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""SkillsRequestProcessor — injects skill overviews and loaded contents."""

from __future__ import annotations

import json
from typing import Any
from typing import List
from typing import Optional

from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.skills import BaseSkillRepository
from trpc_agent_sdk.skills import Skill
from trpc_agent_sdk.skills import SkillLoadModeNames
from trpc_agent_sdk.skills import SkillProfileFlags
from trpc_agent_sdk.skills import SkillProfileNames
from trpc_agent_sdk.skills import SkillRepositoryResolver
from trpc_agent_sdk.skills import SkillToolsNames
from trpc_agent_sdk.skills import docs_scan_prefix
from trpc_agent_sdk.skills import docs_state_key
from trpc_agent_sdk.skills import get_skill_config
from trpc_agent_sdk.skills import loaded_order_state_key
from trpc_agent_sdk.skills import loaded_scan_prefix
from trpc_agent_sdk.skills import loaded_state_key
from trpc_agent_sdk.skills import marshal_loaded_order
from trpc_agent_sdk.skills import parse_loaded_order
from trpc_agent_sdk.skills import set_skill_config
from trpc_agent_sdk.skills import tool_scan_prefix
from trpc_agent_sdk.skills import tool_state_key
from trpc_agent_sdk.skills import touch_loaded_order

from ._skills_tool_result_processor import SKILL_LOADED_RE

# ---------------------------------------------------------------------------
# Prompt section headers (mirrors Go const block)
# ---------------------------------------------------------------------------

_SKILLS_OVERVIEW_HEADER = "Available skills:"
_SKILLS_CAPABILITY_HEADER = "Skill tool availability:"
_SKILLS_TOOLING_GUIDANCE_HEADER = "Tooling and workspace guidance:"

_SKILLS_TURN_INIT_STATE_KEY = "processor:skills:turn_init"


def normalize_load_mode(mode: str) -> str:
    value = (mode or "").strip().lower()
    if value in (SkillLoadModeNames.ONCE, SkillLoadModeNames.TURN, SkillLoadModeNames.SESSION):
        return value
    return SkillLoadModeNames.TURN


def _append_knowledge_guidance(lines: list[str], flags: SkillProfileFlags) -> None:
    """Append docs-loading guidance mirroring Go's appendKnowledgeGuidance."""
    has_list_docs = flags.list_docs
    has_select_docs = flags.select_docs
    if has_list_docs and has_select_docs:
        lines.append("- Use the available doc listing and selection helpers to keep"
                     " documentation loads targeted.\n")
    elif has_list_docs:
        lines.append("- Use the available doc listing helper to discover doc names,"
                     " then load only the docs you need.\n")
    elif has_select_docs:
        lines.append("- If doc names are already known, use the available doc"
                     " selection helper to keep loaded docs targeted.\n")
    else:
        lines.append("- If you need docs, request them directly with skill_load.docs"
                     " or include_all_docs.\n")
    lines.append("- Avoid include_all_docs unless the user asks or the task genuinely"
                 " needs the full doc set.\n")


# ---------------------------------------------------------------------------
# Guidance text builders (mirrors Go defaultXxxGuidance functions)
# ---------------------------------------------------------------------------


def _default_catalog_only_guidance() -> str:
    return (f"\n{_SKILLS_TOOLING_GUIDANCE_HEADER}\n"
            "- Use the skill overview as a catalog only. Built-in skill tools are"
            " unavailable in this configuration; if a task depends on loading or"
            " executing a skill, use other registered tools or explain the"
            " limitation clearly.\n")


def _default_doc_helpers_only_guidance(flags: SkillProfileFlags) -> str:
    lines = [
        "\n",
        _SKILLS_TOOLING_GUIDANCE_HEADER,
        "\n",
    ]
    has_list_docs = flags.list_docs
    has_select_docs = flags.select_docs
    if has_list_docs and has_select_docs:
        lines.append("- Use skills only to inspect available doc names or adjust"
                     " doc selection state.\n")
    elif has_list_docs:
        lines.append("- Use skills only to inspect available doc names.\n")
    elif has_select_docs:
        lines.append("- Use skills only to adjust doc selection when doc names are"
                     " already known.\n")
    lines.append("- Built-in skill loading is unavailable, so doc helpers do not"
                 " inject SKILL.md or doc contents into context; if the task needs"
                 " loaded content or execution, use other registered tools or"
                 " explain the limitation clearly.\n")
    return "".join(lines)


def _default_knowledge_only_guidance(flags: SkillProfileFlags) -> str:
    lines = [
        "\n",
        _SKILLS_TOOLING_GUIDANCE_HEADER,
        "\n",
        "- Use skills for progressive disclosure only: load SKILL.md first,"
        " then inspect only the documentation needed for the current task.\n",
    ]
    _append_knowledge_guidance(lines, flags)
    lines += [
        "- Treat loaded skill content as domain guidance. Do not claim you"
        " executed scripts, shell commands, or interactive flows described by"
        " the skill.\n",
        "- If a skill depends on execution to complete the task, switch to"
        " other registered tools (for example, MCP tools) or explain the"
        " limitation clearly.\n",
    ]
    return "".join(lines)


def _default_full_tooling_and_workspace_guidance(flags: SkillProfileFlags) -> str:
    lines: list[str] = [
        "\n",
        _SKILLS_TOOLING_GUIDANCE_HEADER,
        "\n",
        "- Skills run inside an isolated workspace; you see only files that"
        " are in the workspace or have been staged there by tools.\n",
        "- skill_run runs with CWD at the skill root by default; avoid setting"
        " cwd unless needed.\n",
        "- If you set cwd, use $SKILLS_DIR/$SKILL_NAME (or a subdir)."
        " $SKILLS_DIR alone is the parent dir.\n",
        "- Prefer $WORK_DIR, $OUTPUT_DIR, $RUN_DIR, and $WORKSPACE_DIR over"
        " hard-coded paths.\n",
        "- Treat $WORK_DIR/inputs (and a skill's inputs/ directory) as the"
        " place where tools stage user or host input files. Avoid overwriting"
        " or mutating these inputs directly.\n",
        "- User-uploaded file inputs in the conversation are automatically"
        " staged into $WORK_DIR/inputs when skill_run executes.\n",
        "- When the user mentions external files, directories, artifacts, or"
        " URLs, decide whether to stage them into $WORK_DIR/inputs via"
        " available tools before reading.\n",
        "- To map external files into the workspace, use skill_run inputs"
        " (artifact://, host://, workspace://, skill://). For artifacts, prefer"
        " artifact://name@version; inputs[*].pin=true reuses the first resolved"
        " version (best effort).\n",
        "- Prefer writing new files under $OUTPUT_DIR or a skill's out/"
        " directory and include output_files globs (or an outputs spec) so"
        " files can be collected or saved as artifacts.\n",
        "- Use stdout/stderr for logs or short status text. If the model needs"
        " large or structured text, write it to files under $OUTPUT_DIR and"
        " return it via output_files or outputs.\n",
        "- For Python skills that need third-party packages, create a virtualenv"
        " under the skill's .venv/ directory (it is writable inside the"
        " workspace).\n",
        "- output_files entries are workspace paths/globs (e.g. out/*.txt)."
        " Do not use workspace:// or artifact:// in output_files.\n",
        "- When skill_run returns primary_output or output_files, prefer using"
        " the inline content directly. If you need a stable reference for other"
        " tools, use output_files[*].ref (workspace://...).\n",
        "- Non-text outputs never inline content. Use output_files[*].ref"
        " (workspace://...) to pass them to other tools. For large text outputs,"
        " set omit_inline_content=true so output_files return metadata only,"
        " then use output_files[*].ref with read_file when needed. For"
        " persistence, prefer outputs.save=true with outputs.inline=false; if"
        " you use output_files, set save_as_artifacts=true.\n",
        "- Do not rerun the same skill_run command when you already have the"
        " needed content.\n",
        "- If you already have the needed file content, stop calling file tools"
        " and answer.\n",
        "- When chaining multiple skills, read previous results from $OUTPUT_DIR"
        " (or a skill's out/ directory) instead of copying them back into inputs"
        " directories.\n",
    ]
    if flags.load:
        lines += [
            "- Treat loaded skill docs as guidance, not perfect truth; when runtime"
            " help or stderr disagrees, trust observed runtime behavior.\n",
            "- Loading a skill gives you instructions and bundled resources; it does"
            " not execute the skill by itself.\n",
            "- The skill summaries above are routing summaries only; they do not"
            " replace SKILL.md or other loaded docs.\n",
            "- If the loaded content already provides enough guidance to answer or"
            " produce the requested result, respond directly.\n",
            "- If you decide to use a skill, load SKILL.md before",
        ]
        if flags.requires_exec_session_tools():
            lines.append(" the first skill_run or skill_exec for that skill, then load"
                         " only the docs you still need.\n")
        else:
            lines.append(" the first skill_run for that skill, then load only the docs"
                         " you still need.\n")
        lines += [
            "- Do not infer commands, script entrypoints, or resource layouts from"
            " the short summary alone.\n",
        ]
        _append_knowledge_guidance(lines, flags)
    elif flags.has_doc_helpers():
        lines += [
            "- Built-in skill loading is unavailable in this configuration. Doc"
            " listing or selection helpers can inspect doc names or selection"
            " state, but they do not inject SKILL.md or doc contents into"
            " context.\n",
        ]
    else:
        lines += [
            "- Built-in skill loading is unavailable in this configuration; do not"
            " assume SKILL.md or doc contents are in context.\n",
        ]

    lines += [
        "- Use execution tools only when running a command will reveal or produce"
        " information or files you still need.\n",
    ]
    if flags.requires_exec_session_tools():
        lines.append("- Use skill_exec only when a command needs incremental stdin or"
                     " TTY-style interaction; otherwise prefer one-shot execution.\n")
    else:
        lines.append("- Do not assume interactive execution is available when only"
                     " one-shot execution tools are present.\n")
    lines += [
        "- skill_run is a command runner inside the skill workspace, not a magic"
        " capability. It does not automatically add the skill directory to PATH"
        " or install dependencies; invoke scripts via an explicit interpreter and"
        " path (e.g., python3 scripts/foo.py).\n",
        "- When you execute, follow the tool description, ",
    ]
    if flags.load:
        lines[-1] += "loaded skill docs, "
    lines[-1] += ("bundled scripts, and observed runtime behavior rather than inventing shell"
                  " syntax or command arguments.\n")
    return "".join(lines)


def _default_tooling_and_workspace_guidance(flags: SkillProfileFlags) -> str:
    if not flags.is_any():
        return _default_catalog_only_guidance()

    if not flags.run:
        if flags.load:
            return _default_knowledge_only_guidance(flags)
        if flags.has_doc_helpers():
            return _default_doc_helpers_only_guidance(flags)
        return _default_catalog_only_guidance()
    return _default_full_tooling_and_workspace_guidance(flags)


def _normalize_custom_guidance(guidance: str) -> str:
    if not guidance:
        return ""
    if not guidance.startswith("\n"):
        guidance = "\n" + guidance
    if not guidance.endswith("\n"):
        guidance += "\n"
    return guidance


# ---------------------------------------------------------------------------
# SkillsRequestProcessor
# ---------------------------------------------------------------------------


class SkillsRequestProcessor:
    """Injects skill overviews and loaded contents into LLM requests.

    Args:
        skill_repository:  Default skill repository.
        load_mode:         ``"turn"`` (default), ``"once"``, or ``"session"``.
        tooling_guidance:  ``None`` → use built-in default guidance;
                           ``""``   → omit guidance block;
                           other    → use provided text verbatim.
        tool_result_mode:  When ``True``, skip loaded-skill injection here
                           (content is materialized into tool results instead).
        tool_profile:      Profile string (e.g. ``"knowledge_only"``).
        forbidden_tools: Optional explicit blacklist of built-in skill tools.
        tool_flags:        Optional resolved flags; when set, takes precedence
                           over ``tool_profile``/``forbidden_tools``.
        exec_tools_disabled: When ``True``, omit skill_exec guidance lines.
        repo_resolver:     Optional ``(ctx) -> BaseSkillRepository`` callable
                           that returns an invocation-specific repository.
        max_loaded_skills: Cap on simultaneously loaded skills (0 = no limit).
    """

    def __init__(
        self,
        skill_repository: BaseSkillRepository,
        *,
        load_mode: str = str(SkillLoadModeNames.TURN),
        tooling_guidance: Optional[str] = None,
        tool_result_mode: bool = False,
        tool_profile: str = str(SkillProfileNames.FULL),
        forbidden_tools: Optional[list[str]] = None,
        tool_flags: Optional[SkillProfileFlags] = None,
        exec_tools_disabled: bool = False,
        repo_resolver: Optional[SkillRepositoryResolver] = None,
        max_loaded_skills: int = 0,
    ) -> None:
        self._skill_repository = skill_repository
        self._load_mode = normalize_load_mode(load_mode)
        self._tooling_guidance = tooling_guidance
        self._tool_result_mode = tool_result_mode
        try:
            resolved_flags = tool_flags or SkillProfileFlags.resolve_flags(tool_profile, forbidden_tools)
        except ValueError as ex:
            logger.warning("skills: invalid skill tool flags config, fallback to full profile: %s", ex)
            resolved_flags = SkillProfileFlags.preset_flags(tool_profile, forbidden_tools)
        if exec_tools_disabled:
            resolved_flags = resolved_flags.without_interactive_execution()
        self._tool_flags = resolved_flags
        self._repo_resolver = repo_resolver
        self._max_loaded_skills = max_loaded_skills

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def process_llm_request(
        self,
        ctx: InvocationContext,
        request: LlmRequest,
    ) -> list[str]:
        """Inject skill overview and loaded content into *request*.

        Returns the list of currently-loaded skill names (after any capping).
        """
        if request is None or ctx is None:
            logger.warning("skills: process_llm_request: request or ctx is None")
            return []

        repo = self._get_repository(ctx)
        if repo is None:
            return []

        self._maybe_clear_skill_state_for_turn(ctx)

        # 1) Always inject overview (names + descriptions).
        self._inject_overview(ctx, request, repo)

        loaded = self._get_loaded_skills(ctx)
        loaded = self._maybe_cap_loaded_skills(ctx, loaded)

        if self._tool_result_mode:
            # Materialization is handled by a dedicated post-history processor
            # in request pipeline (Go-aligned ordering).
            return loaded

        # 2) Loaded skills: full body + docs (sorted for stable prompts).
        loaded.sort()

        parts: list[str] = []
        for name in loaded:
            try:
                sk = repo.get(name)
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
            parts.append("Docs loaded: ")
            if not sel:
                parts.append("none\n")
            else:
                parts.append(", ".join(sel) + "\n")
                doc_text = self._build_docs_text(sk, sel)
                if doc_text:
                    parts.append(doc_text)
            tool_sel = self._get_tools_selection(ctx, name)
            parts.append("Tools selected: ")
            if not tool_sel:
                parts.append("none\n")
            else:
                parts.append(", ".join(tool_sel) + "\n")

        if parts:
            self._merge_into_system(request, "".join(parts))

        self._maybe_offload_loaded_skills(ctx, loaded)

        return loaded

    # ------------------------------------------------------------------
    # Repository
    # ------------------------------------------------------------------

    def _get_repository(self, ctx: InvocationContext) -> Optional[BaseSkillRepository]:
        if self._repo_resolver is not None:
            return self._repo_resolver(ctx)
        return self._skill_repository

    # ------------------------------------------------------------------
    # Skill load mode: turn clearing + once offloading
    # ------------------------------------------------------------------

    def _maybe_clear_skill_state_for_turn(self, ctx: InvocationContext) -> None:
        """Clear loaded-skill state at the start of each new invocation (turn mode).

        Uses ``ctx.invocation_id`` to detect when a new invocation has started
        without persisting an extra key to session state.
        """
        if self._load_mode != SkillLoadModeNames.TURN:
            return
        if ctx.agent_context.get_metadata(_SKILLS_TURN_INIT_STATE_KEY):
            return
        ctx.agent_context.with_metadata(_SKILLS_TURN_INIT_STATE_KEY, True)
        self._clear_skill_state(ctx)

    def _clear_skill_state(self, ctx: InvocationContext) -> None:
        """Clear all loaded-skill state keys from the session."""
        loaded_state_prefix = loaded_scan_prefix(ctx)
        docs_state_prefix = docs_scan_prefix(ctx)
        tools_state_prefix = tool_scan_prefix(ctx)
        order_state_key = loaded_order_state_key(ctx)
        state = self._snapshot_state(ctx)
        for k, v in state.items():
            if not v:
                continue
            if any((
                    k.startswith(loaded_state_prefix),
                    k.startswith(docs_state_prefix),
                    k.startswith(tools_state_prefix),
                    k == order_state_key,
            )):
                ctx.actions.state_delta[k] = None

    def _maybe_offload_loaded_skills(self, ctx: InvocationContext, loaded: list[str]) -> None:
        """After injection, clear skill state for once mode."""
        if self._load_mode != SkillLoadModeNames.ONCE or not loaded:
            return
        for name in loaded:
            ctx.actions.state_delta[loaded_state_key(ctx, name)] = None
            ctx.actions.state_delta[docs_state_key(ctx, name)] = None
            ctx.actions.state_delta[tool_state_key(ctx, name)] = None
        ctx.actions.state_delta[loaded_order_state_key(ctx)] = None

    # ------------------------------------------------------------------
    # Max-loaded-skills cap
    # ------------------------------------------------------------------

    def _maybe_cap_loaded_skills(self, ctx: InvocationContext, loaded: list[str]) -> list[str]:
        """Evict least-recently-used skills when over the configured cap."""
        if self._max_loaded_skills <= 0 or len(loaded) <= self._max_loaded_skills:
            return loaded

        order = self._get_loaded_skill_order(ctx, loaded)
        if not order:
            return loaded
        keep_count = self._max_loaded_skills
        keep_set = set(order[-keep_count:])

        kept: list[str] = []
        for name in loaded:
            if name in keep_set:
                kept.append(name)
            else:
                ctx.actions.state_delta[loaded_state_key(ctx, name)] = None
                ctx.actions.state_delta[docs_state_key(ctx, name)] = None
                ctx.actions.state_delta[tool_state_key(ctx, name)] = None
        new_order = [n for n in order if n in keep_set]
        encoded_order = marshal_loaded_order(new_order)
        ctx.actions.state_delta[loaded_order_state_key(ctx)] = encoded_order
        return kept

    def _get_loaded_skill_order(self, ctx: InvocationContext, loaded: list[str]) -> list[str]:
        loaded_set = self._loaded_skill_set(loaded)
        if not loaded_set:
            return []
        order = self._loaded_skill_order_from_state(ctx, loaded_set)
        if len(order) < len(loaded_set):
            order = self._append_skills_to_order_from_events(ctx, order, loaded_set)
        seen = set(order)
        for name in sorted(n for n in loaded_set if n not in seen):
            order.append(name)
        return order

    # ------------------------------------------------------------------
    # Overview injection
    # ------------------------------------------------------------------

    def _inject_overview(self, ctx: InvocationContext, request: LlmRequest, repo: BaseSkillRepository) -> None:
        sums = repo.summaries()
        if not sums:
            return

        # Guard against double-injection within the same invocation.
        if request.config and request.config.system_instruction:
            if _SKILLS_OVERVIEW_HEADER in str(request.config.system_instruction):
                return

        lines: list[str] = [_SKILLS_OVERVIEW_HEADER, "\n"]
        for s in sums:
            lines.append(f"- {s.name}: {s.description}\n")

        capability = self._capability_guidance_text()
        if capability:
            lines.append(capability)

        guidance = self._tooling_guidance_text()
        if guidance:
            lines.append(guidance)

        overview = "".join(lines)

        # Python-unique: prepend repository-level user_prompt when present.
        user_prompt = ""
        if hasattr(repo, "user_prompt"):
            try:
                user_prompt = repo.user_prompt() or ""
            except Exception:  # pylint: disable=broad-except
                pass
        if user_prompt:
            overview = f"{user_prompt}\n\n{overview}"

        request.append_instructions([overview])

    # ------------------------------------------------------------------
    # Guidance text
    # ------------------------------------------------------------------

    def _tooling_guidance_text(self) -> str:
        if self._tooling_guidance is None:
            tool_prompt = _default_tooling_and_workspace_guidance(self._tool_flags)
        else:
            tool_prompt = _normalize_custom_guidance(self._tooling_guidance)
        if self._tool_flags.has_select_tools():
            tool_prompt += """
                - Use the skill_select_tools tool to select tools for the current task only when user asks for it."
            """
        if self._tool_flags.list_skills:
            tool_prompt += """
                - Use the skill_list_skills tool to list skills for the current task only when user asks for it."
            """
        return tool_prompt

    def _capability_guidance_text(self) -> str:
        """Inject capability block for constrained skill-tool profiles."""
        # Omit when caller explicitly cleared guidance.
        if self._tooling_guidance == "" or self._tool_flags.run:
            return ""
        if self._tool_flags.load:
            return (f"\n{_SKILLS_CAPABILITY_HEADER}\n"
                    "- This configuration supports skill discovery and knowledge loading only.\n"
                    "- Built-in skill execution tools are unavailable in the current mode.\n"
                    "- If a loaded skill describes scripts, shell commands, workspace paths,"
                    " generated files, or interactive flows, treat that content as reference"
                    " only. Use other registered tools for real actions, or explain that"
                    " execution is unavailable in the current mode.\n")
        if self._tool_flags.has_doc_helpers():
            return (f"\n{_SKILLS_CAPABILITY_HEADER}\n"
                    "- This configuration supports skill discovery and skill doc inspection only.\n"
                    "- Built-in skill loading and execution tools are unavailable in the"
                    " current mode.\n- Listing or selecting docs does not inject SKILL.md or doc"
                    " contents into model context by itself.\n")
        return (f"\n{_SKILLS_CAPABILITY_HEADER}\n"
                "- This configuration exposes skill summaries only. Built-in skill tools"
                " are unavailable in the current mode.\n"
                "- Treat the skill overview as a catalog of possible capabilities. Use"
                " other registered tools, or explain the limitation clearly when the task"
                " depends on skill loading or execution.\n")

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def _snapshot_state(self, ctx: InvocationContext) -> dict:
        """Return a merged view of session state + pending delta."""
        state = dict(ctx.session_state)
        for k, v in ctx.actions.state_delta.items():
            if v is None:
                state.pop(k, None)
            else:
                state[k] = v
        return state

    def _read_state(self, ctx: InvocationContext, key: str, default=None):
        delta = ctx.actions.state_delta
        if key in delta:
            return delta[key]
        return ctx.session_state.get(key, default)

    # ------------------------------------------------------------------
    # Loaded skill discovery
    # ------------------------------------------------------------------

    def _get_loaded_skills(self, ctx: InvocationContext) -> list[str]:
        """Return names of all currently loaded skills."""
        names: list[str] = []
        state = self._snapshot_state(ctx)
        scan_prefix = loaded_scan_prefix(ctx)
        for k, v in state.items():
            if not k.startswith(scan_prefix) or not v:
                continue
            name = k[len(scan_prefix):].strip()
            if name:
                names.append(name)
        if names:
            return sorted(set(names))
        return []

    # ------------------------------------------------------------------
    # Docs / tools selection
    # ------------------------------------------------------------------

    def _get_docs_selection(self, ctx: InvocationContext, name: str) -> list[str]:
        value = self._read_state(ctx, docs_state_key(ctx, name), default=None)
        if not value:
            return []
        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8")
            except UnicodeDecodeError:
                return []
        if value == "*":
            repo = self._get_repository(ctx)
            if repo is None:
                return []
            try:
                sk = repo.get(name)
                return [doc.path for doc in sk.resources]
            except Exception as ex:  # pylint: disable=broad-except
                logger.warning("Failed to get docs for skill '%s': %s", name, ex)
                return []
        if not isinstance(value, str):
            return []
        try:
            arr = json.loads(value)
        except json.JSONDecodeError:
            return []
        if not isinstance(arr, list):
            return []
        return [doc for doc in arr if isinstance(doc, str) and doc.strip()]

    def _get_tools_selection(self, ctx: InvocationContext, name: str) -> list[str]:
        value = self._read_state(ctx, tool_state_key(ctx, name), default=None)
        if not value:
            return []
        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8")
            except UnicodeDecodeError:
                return []
        if value == "*":
            repo = self._get_repository(ctx)
            if repo is None:
                return []
            try:
                sk = repo.get(name)
                return sk.tools
            except Exception as ex:  # pylint: disable=broad-except
                logger.warning("Failed to get tools for skill '%s': %s", name, ex)
                return []
        if not isinstance(value, str):
            return []
        try:
            arr = json.loads(value)
        except json.JSONDecodeError:
            return []
        if not isinstance(arr, list):
            return []
        return [tool for tool in arr if isinstance(tool, str) and tool.strip()]

    def _loaded_skill_set(self, loaded: list[str]) -> set[str]:
        out: set[str] = set()
        for name in loaded:
            candidate = (name or "").strip()
            if candidate:
                out.add(candidate)
        return out

    def _loaded_skill_order_from_state(self, ctx: InvocationContext, loaded_set: set[str]) -> list[str]:
        order = parse_loaded_order(self._read_state(ctx, loaded_order_state_key(ctx)))
        if not order:
            return []
        out: list[str] = []
        seen: set[str] = set()
        for name in order:
            if name not in loaded_set or name in seen:
                continue
            out.append(name)
            seen.add(name)
        return out

    def _append_skills_to_order_from_events(
        self,
        ctx: InvocationContext,
        order: list[str],
        loaded_set: set[str],
    ) -> list[str]:
        events = list(getattr(ctx.session, "events", []) or [])
        if not events:
            return order
        for event in events:
            if ctx.agent_name and getattr(event, "author", "") != ctx.agent_name:
                continue
            content = getattr(event, "content", None)
            if content is None or not getattr(content, "parts", None):
                continue
            for part in content.parts:
                response = getattr(part, "function_response", None)
                if response is None:
                    continue
                tool_name = (getattr(response, "name", "") or "").strip()
                if tool_name not in (SkillToolsNames.LOAD, SkillToolsNames.SELECT_DOCS):
                    continue
                skill_name = self._skill_name_from_tool_response(tool_name, getattr(response, "response", None))
                if not skill_name or skill_name not in loaded_set:
                    continue
                order = touch_loaded_order(order, skill_name)
        return order

    def _skill_name_from_tool_response(self, tool_name: str, response: Any) -> str:
        if tool_name == str(SkillToolsNames.SELECT_DOCS) and isinstance(response, dict):
            for key in ("skill", "skill_name", "name"):
                value = response.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            return ""
        if tool_name == SkillToolsNames.LOAD:
            if isinstance(response, dict):
                for key in ("skill", "skill_name", "name", "result"):
                    value = response.get(key)
                    if isinstance(value, str) and value.strip():
                        match = SKILL_LOADED_RE.search(value)
                        if match:
                            return match.group(1).strip()
                        if key in ("skill", "skill_name", "name"):
                            return value.strip()
            if isinstance(response, str):
                match = SKILL_LOADED_RE.search(response)
                if match:
                    return match.group(1).strip()
        return ""

    # ------------------------------------------------------------------
    # Doc text assembly
    # ------------------------------------------------------------------

    def _build_docs_text(self, sk: Skill, wanted: List[str]) -> str:
        if sk is None or not sk.resources:
            return ""
        want = set(wanted)
        parts: list[str] = []
        for d in sk.resources:
            if d.path not in want or not d.content:
                continue
            parts.append(f"\n[Doc] {d.path}\n\n{d.content}\n")
        return "".join(parts)

    # ------------------------------------------------------------------
    # System prompt merging
    # ------------------------------------------------------------------

    def _merge_into_system(self, request: LlmRequest, content: str) -> None:
        """Append *content* to the system instruction."""
        if not content:
            return
        request.append_instructions([content])


def set_skill_processor_parameters(agent_context: AgentContext, parameters: dict[str, Any]) -> None:
    """Set the parameters of a skill processor by agent context.

    Args:
        agent_context: AgentContext object
        parameters: Parameters to set
    """
    skill_config = get_skill_config(agent_context)
    skill_config["skill_processor"].update(parameters)
    set_skill_config(agent_context, skill_config)


def get_skill_processor_parameters(agent_context: AgentContext) -> dict[str, Any]:
    """Get the parameters of a skill processor.

    Args:
        invocation_context: InvocationContext object

    Returns:
        Parameters of the skill processor
    """
    skill_config = get_skill_config(agent_context)
    return skill_config["skill_processor"]
