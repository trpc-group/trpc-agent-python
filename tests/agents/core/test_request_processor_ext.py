# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Extended unit tests for RequestProcessor – focuses on methods and branches
not covered by the base test_request_processor.py."""

from __future__ import annotations

import asyncio
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import trpc_agent_sdk.skills as _skills_pkg

if not hasattr(_skills_pkg, "get_skill_processor_parameters"):

    def _compat_get_skill_processor_parameters(agent_context):
        from trpc_agent_sdk.agents.core._skill_processor import get_skill_processor_parameters as _impl
        return _impl(agent_context)

    _skills_pkg.get_skill_processor_parameters = _compat_get_skill_processor_parameters

from trpc_agent_sdk.agents._llm_agent import LlmAgent
from trpc_agent_sdk.agents.core._request_processor import (
    RequestProcessor,
    default_request_processor,
)
from trpc_agent_sdk.context import InvocationContext, create_agent_context
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.models import LLMModel, LlmRequest, LlmResponse, ModelRegistry
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, FunctionCall, FunctionResponse, GenerateContentConfig, Part


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class _MockLLMModel(LLMModel):
    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"test-rp-ext-.*"]

    async def _generate_async_impl(self, request, stream=False, ctx=None):
        yield LlmResponse(content=None)

    def validate_request(self, request):
        pass


@pytest.fixture(scope="module", autouse=True)
def register_test_model():
    original_registry = ModelRegistry._registry.copy()
    ModelRegistry.register(_MockLLMModel)
    yield
    ModelRegistry._registry = original_registry


@pytest.fixture
def processor():
    return RequestProcessor()


@pytest.fixture
def session_service():
    return InMemorySessionService()


@pytest.fixture
def session(session_service):
    return asyncio.run(
        session_service.create_session(app_name="test", user_id="u1", session_id="s_ext")
    )


@pytest.fixture
def agent():
    return LlmAgent(name="ext_agent", model="test-rp-ext-model", instruction="Be helpful")


@pytest.fixture
def ctx(session_service, session, agent):
    return InvocationContext(
        session_service=session_service,
        invocation_id="inv-ext-1",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
        branch="main_branch",
    )


# ---------------------------------------------------------------------------
# _set_generate_content_config
# ---------------------------------------------------------------------------


class TestSetGenerateContentConfig:
    def test_default_config_when_agent_has_none(self, processor, ctx):
        """Config is created as empty GenerateContentConfig when agent has none."""
        request = LlmRequest(model="test-rp-ext-model")
        ctx.agent.generate_content_config = None
        result = processor._set_generate_content_config(request, ctx.agent, ctx)
        assert result is None
        assert request.config is not None

    def test_deep_copies_existing_config(self, processor, ctx):
        """Existing config is deep-copied so mutations don't leak."""
        original = GenerateContentConfig(temperature=0.5)
        ctx.agent.generate_content_config = original
        request = LlmRequest(model="test-rp-ext-model")
        result = processor._set_generate_content_config(request, ctx.agent, ctx)
        assert result is None
        assert request.config is not original
        assert request.config.temperature == 0.5

    def test_output_schema_set_when_no_tools(self, processor, ctx):
        """Output schema is set on request when agent has schema but no tools."""
        ctx.agent.output_schema = {"type": "object"}
        ctx.agent.tools = []
        request = LlmRequest(model="test-rp-ext-model")
        ctx.agent.generate_content_config = None
        with patch(
            "trpc_agent_sdk.agents.core._request_processor.LlmRequest.set_output_schema"
        ) as mock_set:
            processor._set_generate_content_config(request, ctx.agent, ctx)
            mock_set.assert_called_once_with({"type": "object"})

    def test_output_schema_not_set_when_tools_present(self, processor, ctx):
        """Output schema is NOT set via config when agent has tools."""
        ctx.agent.output_schema = {"type": "object"}
        ctx.agent.tools = [MagicMock()]
        request = LlmRequest(model="test-rp-ext-model")
        ctx.agent.generate_content_config = None
        with patch(
            "trpc_agent_sdk.agents.core._request_processor.LlmRequest.set_output_schema"
        ) as mock_set:
            processor._set_generate_content_config(request, ctx.agent, ctx)
            mock_set.assert_not_called()

    def test_returns_error_event_on_exception(self, processor, ctx):
        """Returns an error event when config copy raises."""
        bad_config = MagicMock()
        bad_config.model_copy.side_effect = RuntimeError("copy boom")
        ctx.agent.generate_content_config = bad_config
        request = LlmRequest(model="test-rp-ext-model")
        result = processor._set_generate_content_config(request, ctx.agent, ctx)
        assert result is not None
        assert result.error_code == "config_error"


# ---------------------------------------------------------------------------
# _add_instructions_to_request
# ---------------------------------------------------------------------------


class TestAddInstructionsToRequest:
    @pytest.mark.asyncio
    async def test_global_instruction_string(self, processor, ctx):
        """Global instruction as a string is included in system prompt."""
        root = LlmAgent(name="root", model="test-rp-ext-model", global_instruction="Global rule")
        # Set up parent chain so root_agent property resolves to root
        ctx.agent.parent_agent = root
        request = LlmRequest(model="test-rp-ext-model")
        result = await processor._add_instructions_to_request(ctx.agent, ctx, request)
        assert result is None
        assert "Global rule" in str(request.config.system_instruction)
        # Clean up
        ctx.agent.parent_agent = None

    @pytest.mark.asyncio
    async def test_callable_instruction(self, processor, ctx):
        """Agent instruction as a callable is resolved."""
        ctx.agent.instruction = lambda _ctx: "dynamic instruction"
        request = LlmRequest(model="test-rp-ext-model")
        result = await processor._add_instructions_to_request(ctx.agent, ctx, request)
        assert result is None
        assert "dynamic instruction" in str(request.config.system_instruction)

    @pytest.mark.asyncio
    async def test_async_callable_instruction(self, processor, ctx):
        """Agent instruction as an async callable is awaited."""
        async def async_instr(_ctx):
            return "async instruction"

        ctx.agent.instruction = async_instr
        request = LlmRequest(model="test-rp-ext-model")
        result = await processor._add_instructions_to_request(ctx.agent, ctx, request)
        assert result is None
        assert "async instruction" in str(request.config.system_instruction)

    @pytest.mark.asyncio
    async def test_code_executor_instruction_appended(self, processor, ctx):
        """Code executor instruction is appended when code_executor is set."""
        ctx.agent.code_executor = MagicMock()
        request = LlmRequest(model="test-rp-ext-model")
        result = await processor._add_instructions_to_request(ctx.agent, ctx, request)
        assert result is None
        assert "CODE EXECUTION RESULT" in str(request.config.system_instruction)

    @pytest.mark.asyncio
    async def test_add_name_to_instruction_false_omits_name(self, processor, ctx):
        """When add_name_to_instruction is False, agent name line is omitted."""
        ctx.agent.add_name_to_instruction = False
        request = LlmRequest(model="test-rp-ext-model")
        result = await processor._add_instructions_to_request(ctx.agent, ctx, request)
        assert result is None
        sys_instr = str(request.config.system_instruction)
        assert "who's name is" not in sys_instr

    @pytest.mark.asyncio
    async def test_session_summary_appended(self, processor, ctx):
        """Session summary text is appended to instructions when present."""
        ctx.session_service.get_session_summary = AsyncMock(return_value="previous summary text")
        request = LlmRequest(model="test-rp-ext-model")
        result = await processor._add_instructions_to_request(ctx.agent, ctx, request)
        assert result is None
        assert "previous summary text" in str(request.config.system_instruction)


# ---------------------------------------------------------------------------
# _add_tools_to_request
# ---------------------------------------------------------------------------


class TestAddToolsToRequest:
    @pytest.mark.asyncio
    async def test_no_tools_no_error(self, processor, ctx):
        """No error when agent has no tools and no transfer."""
        ctx.agent.tools = []
        ctx.agent._should_enable_agent_transfer = MagicMock(return_value=False)
        request = LlmRequest(model="test-rp-ext-model")
        result = await processor._add_tools_to_request(ctx.agent, ctx, request)
        assert result is None

    @pytest.mark.asyncio
    async def test_transfer_tool_added_when_enabled(self, processor, ctx):
        """transfer_to_agent tool is added when agent transfer is enabled."""
        ctx.agent.tools = []
        ctx.agent._should_enable_agent_transfer = MagicMock(return_value=True)
        request = LlmRequest(model="test-rp-ext-model")
        with patch("trpc_agent_sdk.agents.core._request_processor.ToolsProcessor") as MockTP:
            instance = MockTP.return_value
            instance.process_llm_request = AsyncMock()
            result = await processor._add_tools_to_request(ctx.agent, ctx, request)
            assert result is None
            MockTP.assert_called_once()
            tools_arg = MockTP.call_args[0][0]
            tool_names = [t.name if hasattr(t, "name") else str(t) for t in tools_arg]
            assert any("transfer_to_agent" in n for n in tool_names)


# ---------------------------------------------------------------------------
# _add_skills_to_request
# ---------------------------------------------------------------------------


class TestAddSkillsToRequest:
    @pytest.mark.asyncio
    async def test_no_skill_repository_returns_none(self, processor, ctx):
        """Returns None (no error) when agent has no skill_repository."""
        ctx.agent.skill_repository = None
        request = LlmRequest(model="test-rp-ext-model")
        result = await processor._add_skills_to_request(ctx.agent, ctx, request, parameters={})
        assert result is None

    @pytest.mark.asyncio
    async def test_skill_processing_error_returns_event(self, processor, ctx):
        """Error during skill processing returns an error event."""
        mock_repo = MagicMock()
        ctx.agent.skill_repository = mock_repo
        with patch("trpc_agent_sdk.agents.core._request_processor.SkillsRequestProcessor") as MockSRP:
            instance = MockSRP.return_value
            instance.process_llm_request = AsyncMock(side_effect=RuntimeError("skill boom"))
            request = LlmRequest(model="test-rp-ext-model")
            result = await processor._add_skills_to_request(ctx.agent, ctx, request, parameters={})
            assert result is not None
            assert result.error_code == "skill_processing_error"


# ---------------------------------------------------------------------------
# _add_agent_transfer_capabilities
# ---------------------------------------------------------------------------


class TestAddAgentTransferCapabilities:
    @pytest.mark.asyncio
    async def test_skipped_when_transfer_disabled(self, processor, ctx):
        """No-op when agent transfer is not enabled."""
        ctx.agent._should_enable_agent_transfer = MagicMock(return_value=False)
        request = LlmRequest(model="test-rp-ext-model")
        result = await processor._add_agent_transfer_capabilities(ctx.agent, ctx, request)
        assert result is None

    @pytest.mark.asyncio
    async def test_error_returns_event(self, processor, ctx):
        """Error during transfer processing returns an error event."""
        ctx.agent._should_enable_agent_transfer = MagicMock(return_value=True)
        request = LlmRequest(model="test-rp-ext-model")
        with patch(
            "trpc_agent_sdk.agents.core._request_processor.default_agent_transfer_processor",
            create=True,
        ) as mock_proc:
            mock_proc.process_agent_transfer = AsyncMock(side_effect=RuntimeError("transfer boom"))
            # The import inside the method means we need to patch the module
            with patch.dict(
                "sys.modules",
                {"trpc_agent_sdk.agents.core._agent_transfer_processor": MagicMock(
                    default_agent_transfer_processor=mock_proc
                )},
            ):
                result = await processor._add_agent_transfer_capabilities(ctx.agent, ctx, request)
                assert result is not None
                assert result.error_code == "agent_transfer_setup_error"


# ---------------------------------------------------------------------------
# _add_conversation_history
# ---------------------------------------------------------------------------


class TestAddConversationHistory:
    @pytest.mark.asyncio
    async def test_no_session_events_returns_none(self, processor, ctx):
        """No error when session has no events."""
        ctx.session.events = []
        request = LlmRequest(model="test-rp-ext-model")
        result = await processor._add_conversation_history(ctx.agent, ctx, request)
        assert result is None
        assert len(request.contents) == 0

    @pytest.mark.asyncio
    async def test_none_session_returns_none(self, processor, ctx):
        """No error when session is None."""
        ctx.session = None
        request = LlmRequest(model="test-rp-ext-model")
        result = await processor._add_conversation_history(ctx.agent, ctx, request)
        assert result is None


# ---------------------------------------------------------------------------
# _add_content_to_request
# ---------------------------------------------------------------------------


class TestAddContentToRequest:
    def test_empty_event_skipped(self, processor):
        """Events with no content are skipped."""
        request = LlmRequest(model="test-rp-ext-model")
        event = Event(invocation_id="inv-1", author="agent", content=None)
        processor._add_content_to_request(request, event)
        assert len(request.contents) == 0

    def test_thought_parts_filtered(self, processor):
        """Parts with thought=True are filtered out when agent has no planner."""
        request = LlmRequest(model="test-rp-ext-model")
        thought_part = Part(text="internal reasoning", thought=True)
        normal_part = Part(text="visible text")
        event = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(role="model", parts=[thought_part, normal_part]),
        )
        agent = MagicMock()
        agent.planner = None
        processor._add_content_to_request(request, event, agent)
        assert len(request.contents) == 1
        assert len(request.contents[0].parts) == 1
        assert request.contents[0].parts[0].text == "visible text"

    def test_thought_parts_kept_when_planner(self, processor):
        """Parts with thought=True are kept when agent has a planner."""
        request = LlmRequest(model="test-rp-ext-model")
        thought_part = Part(text="internal reasoning", thought=True)
        event = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(role="model", parts=[thought_part]),
        )
        agent = MagicMock()
        agent.planner = MagicMock()
        processor._add_content_to_request(request, event, agent)
        assert len(request.contents) == 1

    def test_all_thoughts_filtered_means_nothing_added(self, processor):
        """Content is not added if all parts are thought-only."""
        request = LlmRequest(model="test-rp-ext-model")
        event = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(role="model", parts=[Part(text="thought", thought=True)]),
        )
        agent = MagicMock()
        agent.planner = None
        processor._add_content_to_request(request, event, agent)
        assert len(request.contents) == 0

    def test_default_role_is_user(self, processor):
        """Content with no role defaults to user."""
        request = LlmRequest(model="test-rp-ext-model")
        event = Event(
            invocation_id="inv-1",
            author="user",
            content=Content(parts=[Part(text="hello")]),
        )
        processor._add_content_to_request(request, event)
        assert request.contents[0].role == "user"


# ---------------------------------------------------------------------------
# _resolve_instruction / _resolve_global_instruction
# ---------------------------------------------------------------------------


class TestResolveInstruction:
    @pytest.mark.asyncio
    async def test_string_instruction(self, processor, ctx):
        """String instruction is returned as-is (with template substitution)."""
        ctx.agent.instruction = "Hello world"
        result = await processor._resolve_instruction(ctx.agent, ctx)
        assert result == "Hello world"

    @pytest.mark.asyncio
    async def test_callable_instruction(self, processor, ctx):
        """Callable instruction is called with ctx."""
        ctx.agent.instruction = lambda c: "from callable"
        result = await processor._resolve_instruction(ctx.agent, ctx)
        assert result == "from callable"

    @pytest.mark.asyncio
    async def test_async_callable_instruction(self, processor, ctx):
        """Async callable instruction is awaited."""
        async def instr(c):
            return "from async"

        ctx.agent.instruction = instr
        result = await processor._resolve_instruction(ctx.agent, ctx)
        assert result == "from async"


class TestResolveGlobalInstruction:
    @pytest.mark.asyncio
    async def test_no_global_instruction(self, processor, ctx):
        """Empty string returned when no global instruction."""
        root = LlmAgent(name="root", model="test-rp-ext-model")
        root.global_instruction = None
        result = await processor._resolve_global_instruction(root, ctx)
        assert result == ""

    @pytest.mark.asyncio
    async def test_callable_global_instruction(self, processor, ctx):
        """Callable global instruction is resolved."""
        root = LlmAgent(name="root", model="test-rp-ext-model")
        root.global_instruction = lambda c: "global callable"
        result = await processor._resolve_global_instruction(root, ctx)
        assert result == "global callable"


# ---------------------------------------------------------------------------
# _add_planning_capabilities / _add_output_schema_capabilities
# ---------------------------------------------------------------------------


class TestAddPlanningCapabilities:
    @pytest.mark.asyncio
    async def test_no_planner_returns_none(self, processor, ctx):
        """Returns None when agent has no planner."""
        ctx.agent.planner = None
        request = LlmRequest(model="test-rp-ext-model")
        result = await processor._add_planning_capabilities(ctx.agent, ctx, request)
        assert result is None

    @pytest.mark.asyncio
    async def test_planner_error_returns_event(self, processor, ctx):
        """Returns error event when planner processing raises."""
        ctx.agent.planner = MagicMock()
        request = LlmRequest(model="test-rp-ext-model")
        with patch(
            "trpc_agent_sdk.agents.core._request_processor.default_planning_processor"
        ) as mock_pp:
            mock_pp.process_request.side_effect = RuntimeError("plan boom")
            result = await processor._add_planning_capabilities(ctx.agent, ctx, request)
            assert result is not None
            assert result.error_code == "planning_setup_error"


class TestAddOutputSchemaCapabilities:
    @pytest.mark.asyncio
    async def test_no_schema_returns_none(self, processor, ctx):
        """Returns None when agent has no output_schema."""
        ctx.agent.output_schema = None
        request = LlmRequest(model="test-rp-ext-model")
        result = await processor._add_output_schema_capabilities(ctx.agent, ctx, request)
        assert result is None

    @pytest.mark.asyncio
    async def test_schema_with_tools_calls_processor(self, processor, ctx):
        """Output schema processor is invoked when agent has both schema and tools."""
        ctx.agent.output_schema = {"type": "object"}
        ctx.agent.tools = [MagicMock()]
        request = LlmRequest(model="test-rp-ext-model")
        with patch.dict(
            "sys.modules",
            {"trpc_agent_sdk.agents.core._output_schema_processor": MagicMock(
                default_output_schema_processor=MagicMock(run_async=AsyncMock())
            )},
        ):
            result = await processor._add_output_schema_capabilities(ctx.agent, ctx, request)
            assert result is None


# ---------------------------------------------------------------------------
# _rearrange_events_for_async_function_responses_in_history
# ---------------------------------------------------------------------------


class TestRearrangeEventsForAsyncFunctionResponses:
    def test_plain_events_unchanged(self, processor):
        """Events without function calls or responses pass through."""
        e1 = Event(invocation_id="inv-1", author="user", content=Content(role="user", parts=[Part(text="hi")]))
        e2 = Event(invocation_id="inv-1", author="agent", content=Content(role="model", parts=[Part(text="hello")]))
        result = processor._rearrange_events_for_async_function_responses_in_history([e1, e2])
        assert len(result) == 2

    def test_function_response_placed_after_call(self, processor):
        """Function response event is placed immediately after corresponding call."""
        fc = FunctionCall(name="tool_a", id="fc-1", args={})
        fr = FunctionResponse(name="tool_a", id="fc-1", response={"ok": True})
        call_event = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(role="model", parts=[Part(function_call=fc)]),
        )
        response_event = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(role="user", parts=[Part(function_response=fr)]),
        )
        text_event = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(role="model", parts=[Part(text="done")]),
        )
        result = processor._rearrange_events_for_async_function_responses_in_history(
            [response_event, call_event, text_event]
        )
        assert len(result) == 3
        assert result[0].content.parts[0].function_call is not None
        assert result[1].content.parts[0].function_response is not None


# ---------------------------------------------------------------------------
# _merge_function_response_events
# ---------------------------------------------------------------------------


class TestMergeFunctionResponseEvents:
    def test_empty_list_raises(self, processor):
        """Raises ValueError for empty list."""
        with pytest.raises(ValueError):
            processor._merge_function_response_events([])

    def test_single_event_returned(self, processor):
        """Single event is returned as-is (deep copy)."""
        fr = FunctionResponse(name="t", id="id1", response={"r": 1})
        event = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(parts=[Part(function_response=fr)]),
        )
        merged = processor._merge_function_response_events([event])
        assert len(merged.content.parts) == 1

    def test_multiple_events_merged(self, processor):
        """Multiple response events are merged into one."""
        fr1 = FunctionResponse(name="t1", id="id1", response={"r": 1})
        fr2 = FunctionResponse(name="t2", id="id2", response={"r": 2})
        e1 = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(parts=[Part(function_response=fr1)]),
        )
        e2 = Event(
            invocation_id="inv-1",
            author="agent",
            content=Content(parts=[Part(function_response=fr2)]),
        )
        merged = processor._merge_function_response_events([e1, e2])
        assert len(merged.content.parts) == 2


# ---------------------------------------------------------------------------
# _convert_foreign_event (extended)
# ---------------------------------------------------------------------------


class TestConvertForeignEventExt:
    def test_no_name_when_disabled(self, processor, ctx):
        """Agent name prefix omitted when add_name_to_instruction is False."""
        ctx.agent.add_name_to_instruction = False
        event = Event(
            invocation_id="inv-1",
            author="other_agent",
            content=Content(parts=[Part(text="hi")]),
        )
        converted = processor._convert_foreign_event(event, ctx.agent)
        assert "[other_agent]" not in converted.content.parts[0].text
        assert "hi" in converted.content.parts[0].text

    def test_function_response_converted(self, processor, ctx):
        """Foreign function responses are converted to text context."""
        fr = FunctionResponse(name="search", response={"items": []})
        event = Event(
            invocation_id="inv-1",
            author="other_agent",
            content=Content(parts=[Part(function_response=fr)]),
        )
        converted = processor._convert_foreign_event(event, ctx.agent)
        assert "search" in converted.content.parts[0].text
        assert converted.content.role == "user"

    def test_empty_parts_returns_as_is(self, processor, ctx):
        """Event with empty parts list is returned unchanged."""
        event = Event(
            invocation_id="inv-1",
            author="other",
            content=Content(parts=[]),
        )
        result = processor._convert_foreign_event(event, ctx.agent)
        assert result is event


# ---------------------------------------------------------------------------
# build_request (integration-level)
# ---------------------------------------------------------------------------


class TestBuildRequest:
    @pytest.mark.asyncio
    async def test_override_messages_used(self, processor, ctx):
        """Override messages bypass history building."""
        request = LlmRequest(model="test-rp-ext-model")
        override = [Content(role="user", parts=[Part(text="override")])]
        ctx.agent._should_enable_agent_transfer = MagicMock(return_value=False)
        result = await processor.build_request(request, ctx.agent, ctx, override_messages=override)
        assert result is None
        assert any("override" in str(c) for c in request.contents)

    @pytest.mark.asyncio
    async def test_include_contents_not_default_skips_history(self, processor, ctx):
        """When include_contents != 'default', conversation history is skipped."""
        request = LlmRequest(model="test-rp-ext-model")
        ctx.agent.include_contents = "none"
        ctx.agent._should_enable_agent_transfer = MagicMock(return_value=False)
        result = await processor.build_request(request, ctx.agent, ctx)
        assert result is None

    @pytest.mark.asyncio
    async def test_early_return_on_config_error(self, processor, ctx):
        """build_request returns error event immediately on config failure."""
        bad_config = MagicMock()
        bad_config.model_copy.side_effect = RuntimeError("kaboom")
        ctx.agent.generate_content_config = bad_config
        request = LlmRequest(model="test-rp-ext-model")
        result = await processor.build_request(request, ctx.agent, ctx)
        assert result is not None
        assert result.error_code == "config_error"
