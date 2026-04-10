# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Materialize loaded skill context into skill tool results."""

from __future__ import annotations

import json
import re
from typing import Any
from typing import Callable
from typing import Optional
from typing import Tuple

from trpc_agent_sdk.context import AgentContext
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.skills import BaseSkillRepository
from trpc_agent_sdk.skills import Skill
from trpc_agent_sdk.skills import SkillLoadModeNames
from trpc_agent_sdk.skills import SkillToolsNames
from trpc_agent_sdk.skills import docs_state_key
from trpc_agent_sdk.skills import get_skill_config
from trpc_agent_sdk.skills import get_skill_load_mode
from trpc_agent_sdk.skills import loaded_order_state_key
from trpc_agent_sdk.skills import loaded_scan_prefix
from trpc_agent_sdk.skills import loaded_state_key
from trpc_agent_sdk.skills import set_skill_config

_SKILLS_LOADED_CONTEXT_HEADER = "Loaded skill context:"
_SESSION_SUMMARY_PREFIX = "Here is a brief summary of your previous interactions:"
SKILL_LOADED_RE = re.compile(r"skill\s+'([^']+)'\s+loaded", re.IGNORECASE)


class SkillsToolResultRequestProcessor:
    """Materialize loaded skill content into skill tool results."""

    def __init__(
        self,
        skill_repository: BaseSkillRepository,
        *,
        skip_fallback_on_session_summary: bool = True,
        repo_resolver: Optional[Callable[[InvocationContext], BaseSkillRepository]] = None,
    ) -> None:
        self._skill_repository = skill_repository
        self._repo_resolver = repo_resolver
        self._skip_fallback_on_session_summary = skip_fallback_on_session_summary

    async def process_llm_request(self, ctx: InvocationContext, request: LlmRequest) -> list[str]:
        """Apply loaded-skill materialization to tool results and fallback prompt."""
        if request is None or ctx is None:
            return []
        repo = self._get_repository(ctx)
        if repo is None:
            return []

        loaded = self._get_loaded_skills(ctx)
        if not loaded:
            return []
        loaded.sort()

        tool_calls = self._index_tool_calls(request)
        last_tool_parts = self._last_skill_tool_parts(request, tool_calls)

        materialized: set[str] = set()
        for skill_name, (content_idx, part_idx) in last_tool_parts.items():
            content = request.contents[content_idx]
            part = content.parts[part_idx]
            function_response = part.function_response
            if function_response is None:
                continue
            base = self._response_to_text(getattr(function_response, "response", None))
            rendered = self._build_tool_result_content(ctx, repo, skill_name, base)
            if not rendered:
                continue
            function_response.response = {"result": rendered}
            materialized.add(skill_name)

        fallback = self._build_fallback_system_content(ctx, repo, loaded, materialized)
        if fallback:
            skip_fallback = False
            if self._skip_fallback_on_session_summary:
                skip_fallback = self._has_session_summary(request) and not last_tool_parts
            if not skip_fallback:
                request.append_instructions([fallback])

        self._maybe_offload_loaded_skills(ctx, loaded)
        return loaded

    def _get_repository(self, ctx: InvocationContext) -> Optional[BaseSkillRepository]:
        if self._repo_resolver is not None:
            return self._repo_resolver(ctx)
        return self._skill_repository

    def _snapshot_state(self, ctx: InvocationContext) -> dict[str, Any]:
        state = dict(ctx.session_state)
        for key, value in ctx.actions.state_delta.items():
            if value is None:
                state.pop(key, None)
            else:
                state[key] = value
        return state

    def _read_state(self, ctx: InvocationContext, key: str, default=None):
        if key in ctx.actions.state_delta:
            return ctx.actions.state_delta[key]
        return ctx.session_state.get(key, default)

    def _get_loaded_skills(self, ctx: InvocationContext) -> list[str]:
        names_set: set[str] = set()
        state = self._snapshot_state(ctx)
        scan_prefix = loaded_scan_prefix(ctx)
        for key, value in state.items():
            if not key.startswith(scan_prefix) or not value:
                continue
            name = key[len(scan_prefix):].strip()
            if name:
                names_set.add(name)
        return sorted(names_set)

    def _index_tool_calls(self, request: LlmRequest) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for content in request.contents:
            if content.role not in ("model", "assistant") or not content.parts:
                continue
            for part in content.parts:
                function_call = getattr(part, "function_call", None)
                if function_call is None:
                    continue
                call_id = (getattr(function_call, "id", "") or "").strip()
                if not call_id:
                    continue
                out[call_id] = function_call
        return out

    def _last_skill_tool_parts(
        self,
        request: LlmRequest,
        tool_calls: dict[str, Any],
    ) -> dict[str, Tuple[int, int]]:
        out: dict[str, Tuple[int, int]] = {}
        for content_idx, content in enumerate(request.contents):
            if content.role != "user" or not content.parts:
                continue
            for part_idx, part in enumerate(content.parts):
                function_response = getattr(part, "function_response", None)
                if function_response is None:
                    continue
                tool_name = (getattr(function_response, "name", "") or "").strip()
                if tool_name not in (SkillToolsNames.LOAD, SkillToolsNames.SELECT_DOCS):
                    continue
                skill_name = self._skill_name_from_tool_response(function_response, tool_calls)
                if not skill_name:
                    continue
                out[skill_name] = (content_idx, part_idx)
        return out

    def _skill_name_from_tool_response(self, function_response: Any, tool_calls: dict[str, Any]) -> str:
        response = getattr(function_response, "response", None)
        if isinstance(response, dict):
            for key in ("skill", "skill_name", "name"):
                value = response.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

        call_id = (getattr(function_response, "id", "") or "").strip()
        if call_id and call_id in tool_calls:
            function_call = tool_calls[call_id]
            args = getattr(function_call, "args", None)
            for key in ("skill", "skill_name", "name"):
                value = self._get_arg_value(args, key)
                if value:
                    return value

        return self._parse_loaded_skill_from_text(self._response_to_text(response))

    def _get_arg_value(self, args: Any, key: str) -> str:
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                return ""
        if isinstance(args, dict):
            value = args.get(key)
            if isinstance(value, str):
                return value.strip()
        return ""

    def _parse_loaded_skill_from_text(self, content: str) -> str:
        text = (content or "").strip()
        if not text:
            return ""
        match = SKILL_LOADED_RE.search(text)
        if match:
            return match.group(1).strip()
        lower = text.lower()
        if lower.startswith("loaded:"):
            return text[len("loaded:"):].strip()
        return ""

    def _response_to_text(self, response: Any) -> str:
        if response is None:
            return ""
        if isinstance(response, str):
            return response.strip()
        if isinstance(response, dict):
            result = response.get("result")
            if isinstance(result, str):
                return result.strip()
            if result is not None:
                return str(result).strip()
            return json.dumps(response, ensure_ascii=False).strip()
        return str(response).strip()

    def _is_loaded_tool_stub(self, tool_output: str, skill_name: str) -> bool:
        loaded = self._parse_loaded_skill_from_text(tool_output)
        if not loaded:
            return False
        return loaded.lower() == skill_name.lower()

    def _build_tool_result_content(
        self,
        ctx: InvocationContext,
        repo: BaseSkillRepository,
        skill_name: str,
        tool_output: str,
    ) -> str:
        try:
            sk = repo.get(skill_name)
        except Exception as ex:  # pylint: disable=broad-except
            logger.warning("skills: get %s failed: %s", skill_name, ex)
            return ""
        if sk is None:
            logger.warning("skills: get %s failed: skill not found", skill_name)
            return ""

        parts: list[str] = []
        base = tool_output.strip()
        if base and self._is_loaded_tool_stub(base, skill_name):
            base = ""
        if base:
            parts.append(base)
            parts.append("\n\n")

        if sk.body.strip():
            parts.append(f"[Loaded] {skill_name}\n\n{sk.body}\n")

        selected_docs = self._get_docs_selection(ctx, skill_name, repo)
        parts.append("Docs loaded: ")
        if not selected_docs:
            parts.append("none\n")
        else:
            parts.append(", ".join(selected_docs) + "\n")
            docs_text = self._build_docs_text(sk, selected_docs)
            if docs_text:
                parts.append(docs_text)
        return "".join(parts).strip()

    def _build_fallback_system_content(
        self,
        ctx: InvocationContext,
        repo: BaseSkillRepository,
        loaded: list[str],
        materialized: set[str],
    ) -> str:
        missing = [name for name in loaded if name not in materialized]
        if not missing:
            return ""

        parts: list[str] = [_SKILLS_LOADED_CONTEXT_HEADER, "\n"]
        appended = False
        for name in missing:
            try:
                sk = repo.get(name)
            except Exception as ex:  # pylint: disable=broad-except
                logger.warning("skills: get %s failed: %s", name, ex)
                continue
            if sk is None:
                logger.warning("skills: get %s failed: skill not found", name)
                continue
            if sk.body.strip():
                parts.append(f"\n[Loaded] {name}\n\n{sk.body}\n")
                appended = True
            selected_docs = self._get_docs_selection(ctx, name, repo)
            parts.append("Docs loaded: ")
            if not selected_docs:
                parts.append("none\n")
            else:
                parts.append(", ".join(selected_docs) + "\n")
                docs_text = self._build_docs_text(sk, selected_docs)
                if docs_text:
                    parts.append(docs_text)
                    appended = True
        if not appended:
            return ""
        return "".join(parts).strip()

    def _has_session_summary(self, request: LlmRequest) -> bool:
        if request is None or request.config is None:
            return False
        system_instruction = str(request.config.system_instruction or "")
        return _SESSION_SUMMARY_PREFIX in system_instruction

    def _get_docs_selection(self, ctx: InvocationContext, skill_name: str, repo: BaseSkillRepository) -> list[str]:
        value = self._read_state(ctx, docs_state_key(ctx, skill_name), default=None)
        if not value:
            return []
        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8")
            except UnicodeDecodeError:
                return []
        if value == "*":
            try:
                sk = repo.get(skill_name)
            except Exception as ex:  # pylint: disable=broad-except
                logger.warning("skills: get %s failed: %s", skill_name, ex)
                return []
            if sk is None:
                return []
            return [doc.path for doc in sk.resources]
        if not isinstance(value, str):
            return []
        try:
            arr = json.loads(value)
        except json.JSONDecodeError:
            return []
        if not isinstance(arr, list):
            return []
        return [doc for doc in arr if isinstance(doc, str) and doc.strip()]

    def _build_docs_text(self, sk: Skill, wanted: list[str]) -> str:
        if sk is None or not sk.resources:
            return ""
        want = set(wanted)
        parts: list[str] = []
        for resource in sk.resources:
            if resource.path not in want or not resource.content:
                continue
            parts.append(f"\n[Doc] {resource.path}\n\n{resource.content}\n")
        return "".join(parts)

    def _maybe_offload_loaded_skills(self, ctx: InvocationContext, loaded: list[str]) -> None:
        if get_skill_load_mode(ctx) != SkillLoadModeNames.ONCE or not loaded:
            return
        for skill_name in loaded:
            ctx.actions.state_delta[loaded_state_key(ctx, skill_name)] = None
            ctx.actions.state_delta[docs_state_key(ctx, skill_name)] = None
        ctx.actions.state_delta[loaded_order_state_key(ctx)] = None


def set_skill_tool_result_processor_parameters(agent_context: AgentContext, parameters: dict[str, Any]) -> None:
    """Set the parameters of a skill tool result processor by agent context.

    Args:
        agent_context: AgentContext object
        parameters: Parameters to set
    """
    skill_config = get_skill_config(agent_context)
    skill_config["skills_tool_result_processor"].update(parameters)
    set_skill_config(agent_context, skill_config)


def get_skill_tool_result_processor_parameters(agent_context: AgentContext) -> dict[str, Any]:
    """Get the parameters of a skill tool result processor.

    Args:
        agent_context: AgentContext object

    Returns:
        Parameters of the skill tool result processor
    """
    skill_config = get_skill_config(agent_context)
    return skill_config["skills_tool_result_processor"]
