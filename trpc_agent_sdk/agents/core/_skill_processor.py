# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""SkillsRequestProcessor — injects skill overviews and loaded contents.

Mirrors ``internal/flow/processor/skills.go`` from trpc-agent-go, while
retaining Python-unique features (user_prompt, tools-selection summary).

Behavior
--------
- Overview  : always injected (skill names + descriptions).
- Loaded skills: full SKILL.md body injected into system prompt (or
  deferred to tool-result mode).
- Docs       : doc texts selected via session state keys.
- Tools      : (Python-unique) tool selection summary for each loaded skill.

Skill load modes
----------------
- ``turn``    (default) – loaded skill content is available for all LLM
  calls within the current invocation, then cleared at the start of the
  next invocation.
- ``once``    – loaded skill content is injected once, then offloaded
  (state keys cleared) immediately after injection.
- ``session`` – loaded skill content persists across invocations until
  the session expires or state is cleared explicitly.
"""

from __future__ import annotations

import json
from typing import Callable
from typing import List
from typing import Optional

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.skills import BaseSkillRepository
from trpc_agent_sdk.skills import SKILL_DOCS_STATE_KEY_PREFIX
from trpc_agent_sdk.skills import SKILL_LOADED_STATE_KEY_PREFIX
from trpc_agent_sdk.skills import SKILL_TOOLS_STATE_KEY_PREFIX
from trpc_agent_sdk.skills import Skill
from trpc_agent_sdk.skills import generic_get_selection

# ---------------------------------------------------------------------------
# Load mode constants (mirrors Go SkillLoadModeXxx)
# ---------------------------------------------------------------------------

SKILL_LOAD_MODE_ONCE = "once"
SKILL_LOAD_MODE_TURN = "turn"
SKILL_LOAD_MODE_SESSION = "session"

_DEFAULT_SKILL_LOAD_MODE = SKILL_LOAD_MODE_TURN

# ---------------------------------------------------------------------------
# Prompt section headers (mirrors Go const block)
# ---------------------------------------------------------------------------

_SKILLS_OVERVIEW_HEADER = "Available skills:"
_SKILLS_CAPABILITY_HEADER = "Skill tool availability:"
_SKILLS_TOOLING_GUIDANCE_HEADER = "Tooling and workspace guidance:"

# ---------------------------------------------------------------------------
# Internal state keys
# ---------------------------------------------------------------------------

# JSON array of skill names in load order — used by the max-cap eviction.
_SKILLS_LOADED_ORDER_STATE_KEY = "temp:skill:loaded_order"

# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


def _normalize_load_mode(mode: str) -> str:
    m = (mode or "").strip().lower()
    if m in (SKILL_LOAD_MODE_ONCE, SKILL_LOAD_MODE_TURN, SKILL_LOAD_MODE_SESSION):
        return m
    return _DEFAULT_SKILL_LOAD_MODE


def _is_knowledge_only(profile: str) -> bool:
    """Return True for profiles that support knowledge lookup only."""
    p = (profile or "").strip().lower().replace("-", "_")
    return p in ("knowledge_only", "knowledge")


# ---------------------------------------------------------------------------
# Guidance text builders (mirrors Go defaultXxxGuidance functions)
# ---------------------------------------------------------------------------


def _default_knowledge_only_guidance() -> str:
    return ("\n" + _SKILLS_TOOLING_GUIDANCE_HEADER + "\n" +
            "- Use skills for progressive disclosure only: load SKILL.md first,"
            " then inspect only the documentation needed for the current task.\n" +
            "- Avoid include_all_docs unless the user asks or the task genuinely"
            " needs the full doc set.\n" + "- Treat loaded skill content as domain guidance. Do not claim you"
            " executed scripts, shell commands, or interactive flows described by"
            " the skill.\n" + "- If a skill depends on execution to complete the task, switch to"
            " other registered tools (for example, MCP tools) or explain the"
            " limitation clearly.\n")


def _default_full_tooling_and_workspace_guidance(exec_tools_disabled: bool) -> str:
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
        "- Treat loaded skill docs as guidance, not perfect truth; when runtime"
        " help or stderr disagrees, trust observed runtime behavior.\n",
        "- Loading a skill gives you instructions and bundled resources; it does"
        " not execute the skill by itself.\n",
        "- The skill summaries above are routing summaries only; they do not"
        " replace SKILL.md or other loaded docs.\n",
        "- If the loaded content already provides enough guidance to answer or"
        " produce the requested result, respond directly.\n",
        "- A skill can still be executable even when it has no extra docs"
        " or no custom tools. If SKILL.md provides runnable commands,"
        " proceed with skill_run using those commands.\n",
        "- If a skill is not loaded, call skill_load; you may pass docs or"
        " include_all_docs.\n",
        "- If the body is loaded and docs are missing, treat docs as optional"
        " unless the task explicitly requires extra references; then call"
        " skill_select_docs or skill_load again to add docs.\n",
        "- If the skill defines tools in its SKILL.md, they will be"
        " automatically selected when you load the skill. You can refine tool"
        " selection with skill_select_tools.\n",
        "- If you decide to use a skill, load SKILL.md before",
    ]
    if exec_tools_disabled:
        lines.append(" the first skill_run for that skill, then load only"
                     " the docs you still need.\n")
    else:
        lines.append(" the first skill_run or skill_exec for that skill,"
                     " then load only the docs you still need.\n")
    lines += [
        "- Do not infer commands, script entrypoints, or resource layouts"
        " from the short summary alone.\n",
        "- For docs, prefer skill_list_docs + skill_select_docs to load only"
        " what you need.\n",
        "- Avoid include_all_docs unless you need every doc or the user asks.\n",
        "- Use execution tools only when running a command will reveal or"
        " produce information or files you still need.\n",
    ]
    if not exec_tools_disabled:
        lines.append("- Use skill_exec only when a command needs incremental stdin or"
                     " TTY-style interaction; otherwise prefer one-shot execution.\n")
    else:
        lines.append("- Do not assume interactive execution is available when only"
                     " one-shot execution tools are present.\n")
    lines += [
        "- Prefer script-based commands from SKILL.md examples (for example,"
        " python3 scripts/foo.py) instead of ad-hoc python -c rewrites"
        " unless the skill explicitly recommends inline execution.\n",
        "- skill_run is a command runner inside the skill workspace, not a"
        " magic capability. It does not automatically add the skill directory"
        " to PATH or install dependencies; invoke scripts via an explicit"
        " interpreter and path (e.g., python3 scripts/foo.py).\n",
        "- When you execute, follow the tool description, loaded skill docs,"
        " bundled scripts, and observed runtime behavior rather than inventing"
        " shell syntax or command arguments.\n",
        "- If skill_list_tools returns command_examples, execute one of those"
        " commands directly before trying ad-hoc shell alternatives.\n",
    ]
    return "".join(lines)


def _default_tooling_and_workspace_guidance(profile: str, exec_tools_disabled: bool) -> str:
    if _is_knowledge_only(profile):
        return _default_knowledge_only_guidance()
    return _default_full_tooling_and_workspace_guidance(exec_tools_disabled)


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

    Mirrors Go's ``SkillsRequestProcessor``.

    Args:
        skill_repository:  Default skill repository.
        load_mode:         ``"turn"`` (default), ``"once"``, or ``"session"``.
        tooling_guidance:  ``None`` → use built-in default guidance;
                           ``""``   → omit guidance block;
                           other    → use provided text verbatim.
        tool_result_mode:  When ``True``, skip loaded-skill injection here
                           (content is materialized into tool results instead).
        tool_profile:      Profile string (e.g. ``"knowledge_only"``).
        exec_tools_disabled: When ``True``, omit skill_exec guidance lines.
        repo_resolver:     Optional ``(ctx) -> BaseSkillRepository`` callable
                           that returns an invocation-specific repository.
        max_loaded_skills: Cap on simultaneously loaded skills (0 = no limit).
    """

    def __init__(
        self,
        skill_repository: BaseSkillRepository,
        *,
        load_mode: str = SKILL_LOAD_MODE_TURN,
        tooling_guidance: Optional[str] = None,
        tool_result_mode: bool = False,
        tool_profile: str = "",
        exec_tools_disabled: bool = False,
        repo_resolver: Optional[Callable[[InvocationContext], BaseSkillRepository]] = None,
        max_loaded_skills: int = 0,
    ) -> None:
        self._skill_repository = skill_repository
        self._load_mode = _normalize_load_mode(load_mode)
        self._tooling_guidance = tooling_guidance
        self._tool_result_mode = tool_result_mode
        self._tool_profile = (tool_profile or "").strip()
        self._exec_tools_disabled = exec_tools_disabled
        self._repo_resolver = repo_resolver
        self._max_loaded_skills = max_loaded_skills
        # Tracks which invocation IDs have already had their turn-init clearing
        # applied.  This is ephemeral instance state — not persisted to session.
        self._initialized_invocations: set[str] = set()

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
        self._inject_overview(request, repo)

        loaded = self._get_loaded_skills(ctx)
        loaded = self._maybe_cap_loaded_skills(ctx, loaded)

        if self._tool_result_mode:
            # Loaded skill bodies/docs are injected into tool results by a
            # separate post-content processor — skip injection here.
            return loaded

        # 2) Loaded skills: full body + docs + tools (sorted for stable prompts).
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

            # Tools (Python-unique: skill_select_tools integration)
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
        if self._load_mode != SKILL_LOAD_MODE_TURN:
            return
        inv_id = ctx.invocation_id
        if inv_id in self._initialized_invocations:
            return
        self._initialized_invocations.add(inv_id)
        # Bound the set size to avoid unbounded growth across long-running servers.
        if len(self._initialized_invocations) > 2000:
            oldest = list(self._initialized_invocations)[:1000]
            for old_id in oldest:
                self._initialized_invocations.discard(old_id)
        self._clear_skill_state(ctx)

    def _clear_skill_state(self, ctx: InvocationContext) -> None:
        """Clear all loaded-skill state keys from the session."""
        state = self._snapshot_state(ctx)
        for k, v in state.items():
            if not v:
                continue
            if (k.startswith(SKILL_LOADED_STATE_KEY_PREFIX) or k.startswith(SKILL_DOCS_STATE_KEY_PREFIX)
                    or k.startswith(SKILL_TOOLS_STATE_KEY_PREFIX) or k == _SKILLS_LOADED_ORDER_STATE_KEY):
                ctx.actions.state_delta[k] = None

    def _maybe_offload_loaded_skills(self, ctx: InvocationContext, loaded: list[str]) -> None:
        """After injection, clear skill state for once mode."""
        if self._load_mode != SKILL_LOAD_MODE_ONCE or not loaded:
            return
        for name in loaded:
            ctx.actions.state_delta[SKILL_LOADED_STATE_KEY_PREFIX + name] = None
            ctx.actions.state_delta[SKILL_DOCS_STATE_KEY_PREFIX + name] = None
            ctx.actions.state_delta[SKILL_TOOLS_STATE_KEY_PREFIX + name] = None
        ctx.actions.state_delta[_SKILLS_LOADED_ORDER_STATE_KEY] = None

    # ------------------------------------------------------------------
    # Max-loaded-skills cap
    # ------------------------------------------------------------------

    def _maybe_cap_loaded_skills(self, ctx: InvocationContext, loaded: list[str]) -> list[str]:
        """Evict least-recently-used skills when over the configured cap."""
        if self._max_loaded_skills <= 0 or len(loaded) <= self._max_loaded_skills:
            return loaded

        order = self._get_loaded_skill_order(ctx, loaded)
        # Keep the most recently touched skills (tail of the order list).
        keep_count = self._max_loaded_skills
        keep_set = set(order[-keep_count:]) if len(order) >= keep_count else set(order)

        kept: list[str] = []
        for name in loaded:
            if name in keep_set:
                kept.append(name)
            else:
                ctx.actions.state_delta[SKILL_LOADED_STATE_KEY_PREFIX + name] = None
                ctx.actions.state_delta[SKILL_DOCS_STATE_KEY_PREFIX + name] = None
                ctx.actions.state_delta[SKILL_TOOLS_STATE_KEY_PREFIX + name] = None

        new_order = [n for n in order if n in keep_set]
        ctx.actions.state_delta[_SKILLS_LOADED_ORDER_STATE_KEY] = json.dumps(new_order)
        return kept

    def _get_loaded_skill_order(self, ctx: InvocationContext, loaded: list[str]) -> list[str]:
        """Return skill names in load order (oldest first, most-recent last).

        Reads the persisted order key; fills in any missing names
        alphabetically (mirrors Go's fillLoadedSkillOrderAlphabetically).
        """
        loaded_set = set(loaded)
        raw = self._read_state(ctx, _SKILLS_LOADED_ORDER_STATE_KEY)
        order: list[str] = []
        if raw:
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(parsed, list):
                    order = [n for n in parsed if n in loaded_set]
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        seen = set(order)
        for name in sorted(n for n in loaded_set if n not in seen):
            order.append(name)
        return order

    # ------------------------------------------------------------------
    # Overview injection
    # ------------------------------------------------------------------

    def _inject_overview(self, request: LlmRequest, repo: BaseSkillRepository) -> None:
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
            return _default_tooling_and_workspace_guidance(self._tool_profile, self._exec_tools_disabled)
        return _normalize_custom_guidance(self._tooling_guidance)

    def _capability_guidance_text(self) -> str:
        """Inject capability block for knowledge-only profiles."""
        if not _is_knowledge_only(self._tool_profile):
            return ""
        # Omit when caller explicitly cleared guidance.
        if self._tooling_guidance is not None and self._tooling_guidance == "":
            return ""
        return ("\n" + _SKILLS_CAPABILITY_HEADER + "\n" +
                "- This profile supports skill discovery and knowledge loading only.\n" +
                "- Execution-oriented skill tools are unavailable in the current mode.\n" +
                "- If a loaded skill describes scripts, shell commands, workspace paths,"
                " generated files, or interactive flows, treat that content as reference"
                " only. Use other registered tools for real actions, or explain that"
                " execution is unavailable in the current mode.\n")

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
        for k, v in state.items():
            if not k.startswith(SKILL_LOADED_STATE_KEY_PREFIX) or not v:
                continue
            name = k[len(SKILL_LOADED_STATE_KEY_PREFIX):]
            names.append(name)
        return names

    # ------------------------------------------------------------------
    # Docs and tools selection
    # ------------------------------------------------------------------

    def _get_docs_selection(self, ctx: InvocationContext, name: str) -> list[str]:

        def get_all_docs(skill_name: str) -> list[str]:
            try:
                repo = self._get_repository(ctx)
                sk = repo.get(skill_name) if repo else None
                if sk is None:
                    return []
                return [d.path for d in sk.resources]
            except Exception as ex:  # pylint: disable=broad-except
                logger.warning("Failed to get docs for skill '%s': %s", skill_name, ex)
                return []

        return generic_get_selection(
            ctx=ctx,
            skill_name=name,
            state_key_prefix=SKILL_DOCS_STATE_KEY_PREFIX,
            get_all_items_callback=get_all_docs,
        )

    def _get_tools_selection(self, ctx: InvocationContext, name: str) -> list[str]:
        """Python-unique: return selected tool names for *name*."""

        def get_all_tools(skill_name: str) -> list[str]:
            try:
                repo = self._get_repository(ctx)
                sk = repo.get(skill_name) if repo else None
                if sk is None:
                    return []
                return sk.tools
            except Exception as ex:  # pylint: disable=broad-except
                logger.warning("Failed to get tools for skill '%s': %s", skill_name, ex)
                return []

        return generic_get_selection(
            ctx=ctx,
            skill_name=name,
            state_key_prefix=SKILL_TOOLS_STATE_KEY_PREFIX,
            get_all_items_callback=get_all_tools,
        )

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
