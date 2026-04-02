# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for SkillsRequestProcessor and module-level helpers."""

from __future__ import annotations

import asyncio
import json
from typing import List
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from trpc_agent_sdk.agents.core._skill_processor import (
    SKILL_LOAD_MODE_ONCE,
    SKILL_LOAD_MODE_SESSION,
    SKILL_LOAD_MODE_TURN,
    SkillsRequestProcessor,
    _default_knowledge_only_guidance,
    _default_full_tooling_and_workspace_guidance,
    _default_tooling_and_workspace_guidance,
    _is_knowledge_only,
    _normalize_custom_guidance,
    _normalize_load_mode,
    _SKILLS_LOADED_ORDER_STATE_KEY,
    _SKILLS_OVERVIEW_HEADER,
)
from trpc_agent_sdk.context import InvocationContext, create_agent_context
from trpc_agent_sdk.events import EventActions
from trpc_agent_sdk.models import LLMModel, LlmRequest, LlmResponse, ModelRegistry
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.skills import (
    SKILL_DOCS_STATE_KEY_PREFIX,
    SKILL_LOADED_STATE_KEY_PREFIX,
    SKILL_TOOLS_STATE_KEY_PREFIX,
    BaseSkillRepository,
    Skill,
    SkillResource,
    SkillSummary,
)
from trpc_agent_sdk.types import GenerateContentConfig


# ---------------------------------------------------------------------------
# Helpers and fixtures
# ---------------------------------------------------------------------------

class _MockLLMModel(LLMModel):
    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"test-sp-.*"]

    async def _generate_async_impl(self, request, stream=False, ctx=None):
        yield LlmResponse(content=None)

    def validate_request(self, request):
        pass


class _StubRepo(BaseSkillRepository):
    """In-memory stub for BaseSkillRepository."""

    def __init__(self, skills=None, summaries_list=None, user_prompt_text=""):
        super().__init__(workspace_runtime=MagicMock())
        self._skills = skills or {}
        self._summaries = summaries_list or []
        self._user_prompt_text = user_prompt_text

    def summaries(self) -> list[SkillSummary]:
        return self._summaries

    def get(self, name: str) -> Skill:
        if name not in self._skills:
            raise ValueError(f"Skill not found: {name}")
        return self._skills[name]

    def user_prompt(self) -> str:
        return self._user_prompt_text

    def skill_list(self) -> list[str]:
        return list(self._skills.keys())

    def path(self, name: str) -> str:
        if name not in self._skills:
            raise ValueError(f"Skill not found: {name}")
        return f"/fake/skills/{name}"

    def refresh(self) -> None:
        pass


@pytest.fixture(scope="module", autouse=True)
def register_test_model():
    original_registry = ModelRegistry._registry.copy()
    ModelRegistry.register(_MockLLMModel)
    yield
    ModelRegistry._registry = original_registry


@pytest.fixture
def session_service():
    return InMemorySessionService()


@pytest.fixture
def session(session_service):
    return asyncio.run(
        session_service.create_session(app_name="test", user_id="u1", session_id="sp_sess")
    )


@pytest.fixture
def ctx(session_service, session):
    from trpc_agent_sdk.agents._llm_agent import LlmAgent
    agent = LlmAgent(name="skill_agent", model="test-sp-model")
    return InvocationContext(
        session_service=session_service,
        invocation_id="inv-sp-1",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
        branch="branch_sp",
    )


@pytest.fixture
def sample_skill():
    return Skill(
        summary=SkillSummary(name="code_review", description="Reviews code"),
        body="# Code Review\nReview PR diffs.",
        resources=[
            SkillResource(path="guide.md", content="Review guide content"),
            SkillResource(path="checklist.md", content="Checklist content"),
        ],
        tools=["lint_check", "format_code"],
    )


@pytest.fixture
def sample_repo(sample_skill):
    return _StubRepo(
        skills={"code_review": sample_skill},
        summaries_list=[SkillSummary(name="code_review", description="Reviews code")],
    )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


class TestNormalizeLoadMode:
    def test_valid_modes(self):
        """Valid mode strings are returned as-is (lowered)."""
        assert _normalize_load_mode("once") == SKILL_LOAD_MODE_ONCE
        assert _normalize_load_mode("TURN") == SKILL_LOAD_MODE_TURN
        assert _normalize_load_mode("Session") == SKILL_LOAD_MODE_SESSION

    def test_invalid_mode_defaults_to_turn(self):
        """Invalid mode falls back to turn."""
        assert _normalize_load_mode("bogus") == SKILL_LOAD_MODE_TURN

    def test_empty_mode_defaults_to_turn(self):
        """Empty/None mode falls back to turn."""
        assert _normalize_load_mode("") == SKILL_LOAD_MODE_TURN
        assert _normalize_load_mode(None) == SKILL_LOAD_MODE_TURN


class TestIsKnowledgeOnly:
    def test_knowledge_only_profiles(self):
        """Recognized knowledge-only profile strings return True."""
        assert _is_knowledge_only("knowledge_only") is True
        assert _is_knowledge_only("knowledge") is True
        assert _is_knowledge_only("Knowledge-Only") is True

    def test_non_knowledge_profiles(self):
        """Non-matching profiles return False."""
        assert _is_knowledge_only("full") is False
        assert _is_knowledge_only("") is False
        assert _is_knowledge_only(None) is False


class TestNormalizeCustomGuidance:
    def test_empty_returns_empty(self):
        """Empty string passes through."""
        assert _normalize_custom_guidance("") == ""

    def test_adds_leading_newline(self):
        """Leading newline is added if missing."""
        result = _normalize_custom_guidance("hello")
        assert result.startswith("\n")

    def test_adds_trailing_newline(self):
        """Trailing newline is added if missing."""
        result = _normalize_custom_guidance("hello")
        assert result.endswith("\n")

    def test_existing_newlines_preserved(self):
        """Existing leading/trailing newlines are not doubled."""
        result = _normalize_custom_guidance("\nhello\n")
        assert result == "\nhello\n"


class TestGuidanceTextBuilders:
    def test_knowledge_only_guidance_contains_header(self):
        """Knowledge-only guidance includes the tooling guidance header."""
        text = _default_knowledge_only_guidance()
        assert "Tooling and workspace guidance" in text

    def test_full_tooling_guidance_exec_enabled(self):
        """Full tooling guidance with exec tools enabled mentions skill_exec."""
        text = _default_full_tooling_and_workspace_guidance(exec_tools_disabled=False)
        assert "skill_exec" in text

    def test_full_tooling_guidance_exec_disabled(self):
        """Full tooling guidance with exec tools disabled omits skill_exec mention."""
        text = _default_full_tooling_and_workspace_guidance(exec_tools_disabled=True)
        assert "interactive execution is available" in text

    def test_default_routing_knowledge_only(self):
        """Dispatcher routes to knowledge-only guidance for matching profile."""
        text = _default_tooling_and_workspace_guidance("knowledge_only", False)
        assert "progressive disclosure" in text

    def test_default_routing_full(self):
        """Dispatcher routes to full guidance for non-matching profile."""
        text = _default_tooling_and_workspace_guidance("", False)
        assert "skill_run" in text


# ---------------------------------------------------------------------------
# SkillsRequestProcessor.__init__
# ---------------------------------------------------------------------------


class TestSkillsRequestProcessorInit:
    def test_defaults(self, sample_repo):
        """Default parameters are applied correctly."""
        proc = SkillsRequestProcessor(sample_repo)
        assert proc._load_mode == SKILL_LOAD_MODE_TURN
        assert proc._tool_result_mode is False
        assert proc._max_loaded_skills == 0

    def test_custom_parameters(self, sample_repo):
        """Custom init parameters are stored."""
        proc = SkillsRequestProcessor(
            sample_repo,
            load_mode="once",
            tooling_guidance="custom",
            tool_result_mode=True,
            tool_profile="knowledge_only",
            exec_tools_disabled=True,
            max_loaded_skills=5,
        )
        assert proc._load_mode == SKILL_LOAD_MODE_ONCE
        assert proc._tooling_guidance == "custom"
        assert proc._tool_result_mode is True
        assert proc._tool_profile == "knowledge_only"
        assert proc._exec_tools_disabled is True
        assert proc._max_loaded_skills == 5


# ---------------------------------------------------------------------------
# _get_repository
# ---------------------------------------------------------------------------


class TestGetRepository:
    def test_returns_default_repo(self, sample_repo):
        """Returns default repository when no resolver is set."""
        proc = SkillsRequestProcessor(sample_repo)
        ctx_mock = MagicMock()
        assert proc._get_repository(ctx_mock) is sample_repo

    def test_resolver_overrides(self, sample_repo):
        """Repo resolver takes precedence over the default repository."""
        other_repo = _StubRepo()
        proc = SkillsRequestProcessor(sample_repo, repo_resolver=lambda c: other_repo)
        ctx_mock = MagicMock()
        assert proc._get_repository(ctx_mock) is other_repo


# ---------------------------------------------------------------------------
# _snapshot_state / _read_state
# ---------------------------------------------------------------------------


class TestStateHelpers:
    def test_snapshot_merges_delta(self, ctx, sample_repo):
        """Snapshot merges session state with pending delta."""
        proc = SkillsRequestProcessor(sample_repo)
        ctx.session.state["key_a"] = "from_session"
        ctx.actions.state_delta["key_b"] = "from_delta"
        snap = proc._snapshot_state(ctx)
        assert snap["key_a"] == "from_session"
        assert snap["key_b"] == "from_delta"

    def test_snapshot_delta_none_removes_key(self, ctx, sample_repo):
        """Delta value of None removes the key from snapshot."""
        proc = SkillsRequestProcessor(sample_repo)
        ctx.session.state["key_a"] = "exists"
        ctx.actions.state_delta["key_a"] = None
        snap = proc._snapshot_state(ctx)
        assert "key_a" not in snap

    def test_read_state_from_delta(self, ctx, sample_repo):
        """read_state prefers delta over session state."""
        proc = SkillsRequestProcessor(sample_repo)
        ctx.session.state["k"] = "old"
        ctx.actions.state_delta["k"] = "new"
        assert proc._read_state(ctx, "k") == "new"

    def test_read_state_from_session(self, ctx, sample_repo):
        """read_state falls back to session state when delta has no key."""
        proc = SkillsRequestProcessor(sample_repo)
        ctx.session.state["k"] = "val"
        assert proc._read_state(ctx, "k") == "val"

    def test_read_state_default(self, ctx, sample_repo):
        """read_state returns default when key not in delta or session."""
        proc = SkillsRequestProcessor(sample_repo)
        assert proc._read_state(ctx, "missing", "fallback") == "fallback"


# ---------------------------------------------------------------------------
# _get_loaded_skills
# ---------------------------------------------------------------------------


class TestGetLoadedSkills:
    def test_no_loaded_skills(self, ctx, sample_repo):
        """Returns empty list when no skills are loaded."""
        proc = SkillsRequestProcessor(sample_repo)
        assert proc._get_loaded_skills(ctx) == []

    def test_loaded_skills_detected(self, ctx, sample_repo):
        """Skills with state key prefix are detected."""
        proc = SkillsRequestProcessor(sample_repo)
        ctx.session.state[SKILL_LOADED_STATE_KEY_PREFIX + "code_review"] = "1"
        result = proc._get_loaded_skills(ctx)
        assert "code_review" in result

    def test_falsy_value_ignored(self, ctx, sample_repo):
        """Skills with falsy state values are not returned."""
        proc = SkillsRequestProcessor(sample_repo)
        ctx.session.state[SKILL_LOADED_STATE_KEY_PREFIX + "empty_skill"] = ""
        assert proc._get_loaded_skills(ctx) == []


# ---------------------------------------------------------------------------
# _inject_overview
# ---------------------------------------------------------------------------


class TestInjectOverview:
    def test_overview_injected(self, sample_repo):
        """Overview is injected into system instruction."""
        proc = SkillsRequestProcessor(sample_repo)
        request = LlmRequest(model="test-sp-model")
        proc._inject_overview(request, sample_repo)
        sys_instr = str(request.config.system_instruction)
        assert "code_review" in sys_instr
        assert "Reviews code" in sys_instr

    def test_no_summaries_no_injection(self):
        """No injection when repo has no summaries."""
        repo = _StubRepo(summaries_list=[])
        proc = SkillsRequestProcessor(repo)
        request = LlmRequest(model="test-sp-model")
        proc._inject_overview(request, repo)
        assert request.config is None or request.config.system_instruction is None

    def test_double_injection_guard(self, sample_repo):
        """Overview is not injected twice."""
        proc = SkillsRequestProcessor(sample_repo)
        request = LlmRequest(model="test-sp-model")
        proc._inject_overview(request, sample_repo)
        first_instr = str(request.config.system_instruction)
        proc._inject_overview(request, sample_repo)
        second_instr = str(request.config.system_instruction)
        assert first_instr == second_instr

    def test_user_prompt_prepended(self):
        """Repository user_prompt is prepended to overview."""
        repo = _StubRepo(
            summaries_list=[SkillSummary(name="s1", description="desc")],
            user_prompt_text="Custom prompt",
        )
        proc = SkillsRequestProcessor(repo)
        request = LlmRequest(model="test-sp-model")
        proc._inject_overview(request, repo)
        sys_instr = str(request.config.system_instruction)
        assert sys_instr.index("Custom prompt") < sys_instr.index("s1")


# ---------------------------------------------------------------------------
# _maybe_clear_skill_state_for_turn
# ---------------------------------------------------------------------------


class TestMaybeClearSkillStateForTurn:
    def test_clears_on_first_invocation(self, ctx, sample_repo):
        """State is cleared on first invocation in turn mode."""
        proc = SkillsRequestProcessor(sample_repo, load_mode="turn")
        ctx.session.state[SKILL_LOADED_STATE_KEY_PREFIX + "sk1"] = "1"
        proc._maybe_clear_skill_state_for_turn(ctx)
        assert (SKILL_LOADED_STATE_KEY_PREFIX + "sk1") in ctx.actions.state_delta
        assert ctx.actions.state_delta[SKILL_LOADED_STATE_KEY_PREFIX + "sk1"] is None

    def test_no_clear_on_second_call_same_invocation(self, ctx, sample_repo):
        """State is NOT cleared on second call within same invocation."""
        proc = SkillsRequestProcessor(sample_repo, load_mode="turn")
        proc._maybe_clear_skill_state_for_turn(ctx)
        ctx.actions.state_delta.clear()
        ctx.session.state[SKILL_LOADED_STATE_KEY_PREFIX + "sk2"] = "1"
        proc._maybe_clear_skill_state_for_turn(ctx)
        assert (SKILL_LOADED_STATE_KEY_PREFIX + "sk2") not in ctx.actions.state_delta

    def test_no_clear_in_session_mode(self, ctx, sample_repo):
        """No clearing in session mode."""
        proc = SkillsRequestProcessor(sample_repo, load_mode="session")
        ctx.session.state[SKILL_LOADED_STATE_KEY_PREFIX + "sk1"] = "1"
        proc._maybe_clear_skill_state_for_turn(ctx)
        assert (SKILL_LOADED_STATE_KEY_PREFIX + "sk1") not in ctx.actions.state_delta


# ---------------------------------------------------------------------------
# _maybe_offload_loaded_skills
# ---------------------------------------------------------------------------


class TestMaybeOffloadLoadedSkills:
    def test_offloads_in_once_mode(self, ctx, sample_repo):
        """Skill state is cleared after injection in once mode."""
        proc = SkillsRequestProcessor(sample_repo, load_mode="once")
        proc._maybe_offload_loaded_skills(ctx, ["code_review"])
        assert ctx.actions.state_delta[SKILL_LOADED_STATE_KEY_PREFIX + "code_review"] is None
        assert ctx.actions.state_delta[SKILL_DOCS_STATE_KEY_PREFIX + "code_review"] is None
        assert ctx.actions.state_delta[SKILL_TOOLS_STATE_KEY_PREFIX + "code_review"] is None
        assert ctx.actions.state_delta[_SKILLS_LOADED_ORDER_STATE_KEY] is None

    def test_no_offload_in_turn_mode(self, ctx, sample_repo):
        """No offloading in turn mode."""
        proc = SkillsRequestProcessor(sample_repo, load_mode="turn")
        proc._maybe_offload_loaded_skills(ctx, ["code_review"])
        assert SKILL_LOADED_STATE_KEY_PREFIX + "code_review" not in ctx.actions.state_delta

    def test_no_offload_when_empty(self, ctx, sample_repo):
        """No offloading when loaded list is empty."""
        proc = SkillsRequestProcessor(sample_repo, load_mode="once")
        proc._maybe_offload_loaded_skills(ctx, [])
        assert len(ctx.actions.state_delta) == 0


# ---------------------------------------------------------------------------
# _maybe_cap_loaded_skills
# ---------------------------------------------------------------------------


class TestMaybeCapLoadedSkills:
    def test_no_cap_returns_all(self, ctx, sample_repo):
        """All skills returned when cap is 0 (disabled)."""
        proc = SkillsRequestProcessor(sample_repo, max_loaded_skills=0)
        result = proc._maybe_cap_loaded_skills(ctx, ["a", "b", "c"])
        assert result == ["a", "b", "c"]

    def test_under_cap_returns_all(self, ctx, sample_repo):
        """All skills returned when count is at or under cap."""
        proc = SkillsRequestProcessor(sample_repo, max_loaded_skills=5)
        result = proc._maybe_cap_loaded_skills(ctx, ["a", "b"])
        assert result == ["a", "b"]

    def test_over_cap_evicts_oldest(self, ctx, sample_repo):
        """Excess skills are evicted, keeping most recent."""
        proc = SkillsRequestProcessor(sample_repo, max_loaded_skills=2)
        ctx.actions.state_delta[_SKILLS_LOADED_ORDER_STATE_KEY] = json.dumps(["a", "b", "c"])
        result = proc._maybe_cap_loaded_skills(ctx, ["a", "b", "c"])
        assert len(result) == 2
        assert "a" not in result
        assert "b" in result
        assert "c" in result


# ---------------------------------------------------------------------------
# _build_docs_text
# ---------------------------------------------------------------------------


class TestBuildDocsText:
    def test_selected_docs_included(self, sample_repo, sample_skill):
        """Only selected docs are included in output."""
        proc = SkillsRequestProcessor(sample_repo)
        text = proc._build_docs_text(sample_skill, ["guide.md"])
        assert "Review guide content" in text
        assert "Checklist content" not in text

    def test_no_docs_returns_empty(self, sample_repo):
        """Empty string for skill with no resources."""
        proc = SkillsRequestProcessor(sample_repo)
        empty_skill = Skill(summary=SkillSummary(name="empty", description=""))
        assert proc._build_docs_text(empty_skill, ["any.md"]) == ""

    def test_none_skill_returns_empty(self, sample_repo):
        """None skill returns empty string."""
        proc = SkillsRequestProcessor(sample_repo)
        assert proc._build_docs_text(None, ["any.md"]) == ""


# ---------------------------------------------------------------------------
# _merge_into_system
# ---------------------------------------------------------------------------


class TestMergeIntoSystem:
    def test_appends_to_system(self, sample_repo):
        """Content is appended to system instruction."""
        proc = SkillsRequestProcessor(sample_repo)
        request = LlmRequest(model="test-sp-model")
        proc._merge_into_system(request, "extra guidance")
        assert "extra guidance" in str(request.config.system_instruction)

    def test_empty_content_no_op(self, sample_repo):
        """Empty string is a no-op."""
        proc = SkillsRequestProcessor(sample_repo)
        request = LlmRequest(model="test-sp-model")
        proc._merge_into_system(request, "")
        assert request.config is None or request.config.system_instruction is None


# ---------------------------------------------------------------------------
# _capability_guidance_text
# ---------------------------------------------------------------------------


class TestCapabilityGuidanceText:
    def test_non_knowledge_profile_empty(self, sample_repo):
        """Non-knowledge profile returns empty string."""
        proc = SkillsRequestProcessor(sample_repo, tool_profile="full")
        assert proc._capability_guidance_text() == ""

    def test_knowledge_profile_returns_guidance(self, sample_repo):
        """Knowledge-only profile returns capability guidance."""
        proc = SkillsRequestProcessor(sample_repo, tool_profile="knowledge_only")
        text = proc._capability_guidance_text()
        assert "knowledge loading only" in text

    def test_knowledge_profile_with_empty_guidance_suppressed(self, sample_repo):
        """Knowledge profile with explicit empty guidance suppresses capability block."""
        proc = SkillsRequestProcessor(sample_repo, tool_profile="knowledge_only", tooling_guidance="")
        assert proc._capability_guidance_text() == ""


# ---------------------------------------------------------------------------
# process_llm_request (integration)
# ---------------------------------------------------------------------------


class TestProcessLlmRequest:
    @pytest.mark.asyncio
    async def test_none_request_returns_empty(self, ctx, sample_repo):
        """Returns empty list for None request."""
        proc = SkillsRequestProcessor(sample_repo)
        result = await proc.process_llm_request(ctx, None)
        assert result == []

    @pytest.mark.asyncio
    async def test_none_ctx_returns_empty(self, sample_repo):
        """Returns empty list for None ctx."""
        proc = SkillsRequestProcessor(sample_repo)
        request = LlmRequest(model="test-sp-model")
        result = await proc.process_llm_request(None, request)
        assert result == []

    @pytest.mark.asyncio
    async def test_overview_injected_no_loaded(self, ctx, sample_repo):
        """Overview is injected even when no skills are loaded."""
        proc = SkillsRequestProcessor(sample_repo)
        request = LlmRequest(model="test-sp-model")
        result = await proc.process_llm_request(ctx, request)
        assert result == []
        assert "code_review" in str(request.config.system_instruction)

    @pytest.mark.asyncio
    async def test_loaded_skill_body_injected(self, ctx, sample_repo):
        """Loaded skill body is injected into system instruction."""
        proc = SkillsRequestProcessor(sample_repo, load_mode="session")
        ctx.session.state[SKILL_LOADED_STATE_KEY_PREFIX + "code_review"] = "1"
        request = LlmRequest(model="test-sp-model")
        result = await proc.process_llm_request(ctx, request)
        assert "code_review" in result
        sys_instr = str(request.config.system_instruction)
        assert "Review PR diffs" in sys_instr

    @pytest.mark.asyncio
    async def test_tool_result_mode_skips_body_injection(self, ctx, sample_repo):
        """In tool_result_mode, loaded skill bodies are NOT injected."""
        proc = SkillsRequestProcessor(sample_repo, tool_result_mode=True, load_mode="session")
        ctx.session.state[SKILL_LOADED_STATE_KEY_PREFIX + "code_review"] = "1"
        request = LlmRequest(model="test-sp-model")
        result = await proc.process_llm_request(ctx, request)
        assert "code_review" in result
        sys_instr = str(request.config.system_instruction) if request.config and request.config.system_instruction else ""
        assert "Review PR diffs" not in sys_instr

    @pytest.mark.asyncio
    async def test_once_mode_offloads_after_injection(self, ctx, sample_repo):
        """In once mode, skill state is cleared after injection."""
        proc = SkillsRequestProcessor(sample_repo, load_mode="once")
        ctx.session.state[SKILL_LOADED_STATE_KEY_PREFIX + "code_review"] = "1"
        request = LlmRequest(model="test-sp-model")
        await proc.process_llm_request(ctx, request)
        assert ctx.actions.state_delta.get(SKILL_LOADED_STATE_KEY_PREFIX + "code_review") is None

    @pytest.mark.asyncio
    async def test_none_repo_returns_empty(self, ctx):
        """Returns empty list when repository resolves to None."""
        proc = SkillsRequestProcessor(
            MagicMock(),
            repo_resolver=lambda c: None,
        )
        request = LlmRequest(model="test-sp-model")
        result = await proc.process_llm_request(ctx, request)
        assert result == []


# ---------------------------------------------------------------------------
# _get_loaded_skill_order
# ---------------------------------------------------------------------------


class TestGetLoadedSkillOrder:
    def test_no_persisted_order(self, ctx, sample_repo):
        """Missing order key returns alphabetical order."""
        proc = SkillsRequestProcessor(sample_repo)
        order = proc._get_loaded_skill_order(ctx, ["beta", "alpha"])
        assert order == ["alpha", "beta"]

    def test_persisted_order_respected(self, ctx, sample_repo):
        """Persisted order is respected for known skills."""
        proc = SkillsRequestProcessor(sample_repo)
        ctx.session.state[_SKILLS_LOADED_ORDER_STATE_KEY] = json.dumps(["beta", "alpha"])
        order = proc._get_loaded_skill_order(ctx, ["alpha", "beta"])
        assert order == ["beta", "alpha"]

    def test_new_skills_appended_alphabetically(self, ctx, sample_repo):
        """Skills not in persisted order are appended alphabetically."""
        proc = SkillsRequestProcessor(sample_repo)
        ctx.session.state[_SKILLS_LOADED_ORDER_STATE_KEY] = json.dumps(["beta"])
        order = proc._get_loaded_skill_order(ctx, ["alpha", "beta", "gamma"])
        assert order == ["beta", "alpha", "gamma"]

    def test_invalid_json_falls_back(self, ctx, sample_repo):
        """Invalid JSON in order key falls back to alphabetical."""
        proc = SkillsRequestProcessor(sample_repo)
        ctx.session.state[_SKILLS_LOADED_ORDER_STATE_KEY] = "not-json"
        order = proc._get_loaded_skill_order(ctx, ["c", "a", "b"])
        assert order == ["a", "b", "c"]
