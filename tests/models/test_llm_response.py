# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

import pytest
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.types import Candidate
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import GenerateContentResponse
from trpc_agent_sdk.types import GenerateContentResponsePromptFeedback
from trpc_agent_sdk.types import GenerateContentResponseUsageMetadata
from trpc_agent_sdk.types import GroundingMetadata
from trpc_agent_sdk.types import Part


class TestLlmResponse:
    """Test suite for LlmResponse class."""

    def test_create_with_valid_candidate_and_content(self):
        """Test creating LlmResponse with valid candidate containing content."""
        # Create test data
        content = Content(parts=[Part.from_text(text="Test response")], role="model")
        grounding_metadata = GroundingMetadata(grounding_chunks=[], grounding_supports=[])
        usage_metadata = GenerateContentResponseUsageMetadata(prompt_token_count=10,
                                                              candidates_token_count=5,
                                                              total_token_count=15)

        candidate = Candidate(content=content, grounding_metadata=grounding_metadata, finish_reason="STOP")

        response = GenerateContentResponse(candidates=[candidate], usage_metadata=usage_metadata)

        # Create LlmResponse
        llm_response = LlmResponse().create(response)

        # Verify the response
        assert llm_response.content is not None
        assert llm_response.content.parts[0].text == "Test response"
        assert llm_response.grounding_metadata == grounding_metadata
        assert llm_response.usage_metadata == usage_metadata
        assert llm_response.error_code is None
        assert llm_response.error_message is None

    def test_create_with_empty_content_parts(self):
        """Test creating LlmResponse when candidate has empty content parts."""
        usage_metadata = GenerateContentResponseUsageMetadata(prompt_token_count=10,
                                                              candidates_token_count=0,
                                                              total_token_count=10)

        # Create candidate with empty parts
        content = Content(parts=[], role="model")
        candidate = Candidate(content=content, finish_reason="MAX_TOKENS", finish_message="Max tokens reached")

        response = GenerateContentResponse(candidates=[candidate], usage_metadata=usage_metadata)

        # Create LlmResponse
        llm_response = LlmResponse().create(response)

        # Verify error response
        assert llm_response.content is None
        assert llm_response.error_code == "MAX_TOKENS"
        assert llm_response.error_message == "Max tokens reached"
        assert llm_response.usage_metadata == usage_metadata

    def test_create_with_no_content(self):
        """Test creating LlmResponse when candidate has no content."""
        usage_metadata = GenerateContentResponseUsageMetadata(prompt_token_count=10,
                                                              candidates_token_count=0,
                                                              total_token_count=10)

        candidate = Candidate(content=None, finish_reason="UNEXPECTED_TOOL_CALL", finish_message="unexpected tool call")

        response = GenerateContentResponse(candidates=[candidate], usage_metadata=usage_metadata)

        # Create LlmResponse
        llm_response = LlmResponse().create(response)

        # Verify error response
        assert llm_response.content is None
        assert llm_response.error_code == "UNEXPECTED_TOOL_CALL"
        assert llm_response.error_message == "unexpected tool call"
        assert llm_response.usage_metadata == usage_metadata

    def test_create_with_no_candidates_and_prompt_feedback(self):
        """Test creating LlmResponse with no candidates but prompt feedback."""
        usage_metadata = GenerateContentResponseUsageMetadata(prompt_token_count=10,
                                                              candidates_token_count=0,
                                                              total_token_count=10)

        prompt_feedback = GenerateContentResponsePromptFeedback(
            block_reason="SAFETY", block_reason_message="Content blocked for safety reasons")

        response = GenerateContentResponse(candidates=[],
                                           prompt_feedback=prompt_feedback,
                                           usage_metadata=usage_metadata)

        # Create LlmResponse
        llm_response = LlmResponse().create(response)

        # Verify error response with prompt feedback
        assert llm_response.content is None
        assert llm_response.error_code == "SAFETY"
        assert llm_response.error_message == "Content blocked for safety reasons"
        assert llm_response.usage_metadata == usage_metadata

    def test_create_with_no_candidates_and_no_prompt_feedback(self):
        """Test creating LlmResponse with no candidates and no prompt feedback."""
        usage_metadata = GenerateContentResponseUsageMetadata(prompt_token_count=10,
                                                              candidates_token_count=0,
                                                              total_token_count=10)

        response = GenerateContentResponse(candidates=[], usage_metadata=usage_metadata)

        # Create LlmResponse
        llm_response = LlmResponse().create(response)

        # Verify unknown error response
        assert llm_response.content is None
        assert llm_response.error_code == "UNKNOWN_ERROR"
        assert llm_response.error_message == "Unknown error."
        assert llm_response.usage_metadata == usage_metadata

    def test_create_preserves_usage_metadata(self):
        """Test that create method preserves usage metadata in all scenarios."""
        usage_metadata = GenerateContentResponseUsageMetadata(prompt_token_count=100,
                                                              candidates_token_count=50,
                                                              total_token_count=150)

        # Test with valid content
        content = Content(parts=[Part.from_text(text="Response")], role="model")
        candidate = Candidate(content=content)
        response1 = GenerateContentResponse(candidates=[candidate], usage_metadata=usage_metadata)
        llm_response1 = LlmResponse().create(response1)
        assert llm_response1.usage_metadata.total_token_count == 150

        # Test with error
        candidate2 = Candidate(content=None,
                               finish_reason="UNEXPECTED_TOOL_CALL",
                               finish_message="unexpected tool call")
        response2 = GenerateContentResponse(candidates=[candidate2], usage_metadata=usage_metadata)
        llm_response2 = LlmResponse().create(response2)
        assert llm_response2.usage_metadata.total_token_count == 150

        # Test with prompt feedback
        prompt_feedback = GenerateContentResponsePromptFeedback(block_reason="SAFETY")
        response3 = GenerateContentResponse(candidates=[],
                                            prompt_feedback=prompt_feedback,
                                            usage_metadata=usage_metadata)
        llm_response3 = LlmResponse().create(response3)
        assert llm_response3.usage_metadata.total_token_count == 150

    def test_create_with_multiple_parts_in_content(self):
        """Test creating LlmResponse with content having multiple parts."""
        parts = [Part.from_text(text="Part 1"), Part.from_text(text="Part 2"), Part.from_text(text="Part 3")]
        content = Content(parts=parts, role="model")
        candidate = Candidate(content=content)

        usage_metadata = GenerateContentResponseUsageMetadata(prompt_token_count=10,
                                                              candidates_token_count=20,
                                                              total_token_count=30)

        response = GenerateContentResponse(candidates=[candidate], usage_metadata=usage_metadata)

        # Create LlmResponse
        llm_response = LlmResponse().create(response)

        # Verify all parts are preserved
        assert llm_response.content is not None
        assert len(llm_response.content.parts) == 3
        assert llm_response.content.parts[0].text == "Part 1"
        assert llm_response.content.parts[1].text == "Part 2"
        assert llm_response.content.parts[2].text == "Part 3"

    def test_create_uses_first_candidate_only(self):
        """Test that only the first candidate is used when multiple candidates exist."""
        content1 = Content(parts=[Part.from_text(text="First response")], role="model")
        content2 = Content(parts=[Part.from_text(text="Second response")], role="model")

        candidate1 = Candidate(content=content1)
        candidate2 = Candidate(content=content2)

        usage_metadata = GenerateContentResponseUsageMetadata(prompt_token_count=10,
                                                              candidates_token_count=50,
                                                              total_token_count=60)

        response = GenerateContentResponse(candidates=[candidate1, candidate2], usage_metadata=usage_metadata)

        # Create LlmResponse
        llm_response = LlmResponse().create(response)

        # Should use first candidate
        assert llm_response.content is not None
        assert llm_response.content.parts[0].text == "First response"

    @pytest.mark.parametrize("finish_reason,finish_message", [
        ("STOP", "Finished with STOP"),
        ("MAX_TOKENS", "Finished with MAX_TOKENS"),
        ("RECITATION", "Finished with RECITATION"),
        ("SAFETY", "Finished with SAFETY"),
        ("SAFETY", None),
    ])
    def test_create_with_different_finish_reasons(self, finish_reason, finish_message):
        """Test creating LlmResponse with various finish reasons."""
        candidate = Candidate(content=None, finish_reason=finish_reason, finish_message=finish_message)

        response = GenerateContentResponse(candidates=[candidate], usage_metadata=None)

        llm_response = LlmResponse().create(response)

        assert llm_response.error_code == finish_reason
        assert llm_response.error_message == finish_message
        assert llm_response.content is None

    @pytest.mark.parametrize("block_reason,block_reason_message", [
        ("SAFETY", "Blocked due to SAFETY"),
        ("OTHER", "Blocked due to OTHER"),
        ("SAFETY", None),
    ])
    def test_create_with_different_block_reasons(self, block_reason, block_reason_message):
        """Test creating LlmResponse with various block reasons in prompt feedback."""
        prompt_feedback = GenerateContentResponsePromptFeedback(block_reason=block_reason,
                                                                block_reason_message=block_reason_message)

        response = GenerateContentResponse(candidates=[], prompt_feedback=prompt_feedback, usage_metadata=None)

        llm_response = LlmResponse().create(response)

        assert llm_response.error_code == block_reason
        if block_reason_message:
            assert block_reason_message in llm_response.error_message
        else:
            assert llm_response.error_message is None

    def test_create_with_none_grounding_metadata(self):
        """Test creating LlmResponse when grounding_metadata is None."""
        content = Content(parts=[Part.from_text(text="Test response")], role="model")
        candidate = Candidate(content=content, grounding_metadata=None)

        usage_metadata = GenerateContentResponseUsageMetadata(prompt_token_count=10,
                                                              candidates_token_count=10,
                                                              total_token_count=20)

        response = GenerateContentResponse(candidates=[candidate], usage_metadata=usage_metadata)

        llm_response = LlmResponse().create(response)

        assert llm_response.content is not None
        assert llm_response.grounding_metadata is None
        assert llm_response.content.parts[0].text == "Test response"

    def test_create_error_response_preserves_all_fields(self):
        """Test that error responses preserve all metadata fields."""
        usage_metadata = GenerateContentResponseUsageMetadata(prompt_token_count=100,
                                                              candidates_token_count=0,
                                                              total_token_count=100)

        candidate = Candidate(content=None,
                              finish_reason="SAFETY",
                              finish_message="Content policy violation detected",
                              grounding_metadata=GroundingMetadata(grounding_chunks=[], grounding_supports=[]))

        response = GenerateContentResponse(candidates=[candidate], usage_metadata=usage_metadata)

        llm_response = LlmResponse().create(response)

        # Verify error fields
        assert llm_response.error_code == "SAFETY"
        assert llm_response.error_message == "Content policy violation detected"
        # Verify metadata is preserved
        assert llm_response.usage_metadata.prompt_token_count == 100
        assert llm_response.usage_metadata.total_token_count == 100

    def test_create_candidate_with_empty_parts_list(self):
        """Test candidate with explicitly empty parts list (not just None content)."""
        content = Content(parts=[], role="model")  # Empty parts
        candidate = Candidate(content=content, finish_reason="STOP")

        response = GenerateContentResponse(candidates=[candidate])

        llm_response = LlmResponse().create(response)

        # Should be treated as error since parts is empty
        assert llm_response.content is None
        assert llm_response.error_code == "STOP"

    def test_create_with_candidate_no_finish_reason(self):
        """Test creating LlmResponse with candidate missing finish_reason."""
        content = Content(parts=[Part.from_text(text="Response")], role="model")
        candidate = Candidate(content=content, finish_reason=None)

        response = GenerateContentResponse(candidates=[candidate])

        llm_response = LlmResponse().create(response)

        assert llm_response.content is not None
        assert llm_response.content.parts[0].text == "Response"
        # finish_reason=None should not cause error when content exists


class TestLlmResponseHasContent:
    """Test suite for :meth:`LlmResponse.has_content`."""

    def test_none_content_is_false(self):
        assert LlmResponse(content=None).has_content() is False

    def test_empty_parts_is_false(self):
        assert LlmResponse(content=Content(parts=[], role="model")).has_content() is False

    def test_text_part_is_true(self):
        content = Content(parts=[Part.from_text(text="hello")], role="model")
        assert LlmResponse(content=content).has_content() is True

    def test_function_call_part_is_true(self):
        content = Content(
            parts=[Part.from_function_call(name="calc", args={"x": 1})],
            role="model",
        )
        assert LlmResponse(content=content).has_content() is True

    def test_function_response_only_is_false(self):
        """Function responses are not user-visible content per the contract."""
        content = Content(
            parts=[Part.from_function_response(name="calc", response={"ok": True})],
            role="tool",
        )
        assert LlmResponse(content=content).has_content() is False

    def test_empty_text_with_function_call_is_true(self):
        content = Content(
            parts=[
                Part(text=""),
                Part.from_function_call(name="calc", args={}),
            ],
            role="model",
        )
        assert LlmResponse(content=content).has_content() is True

    def test_whitespace_text_only_is_true(self):
        """Any non-empty text counts as visible content."""
        content = Content(parts=[Part(text=" ")], role="model")
        assert LlmResponse(content=content).has_content() is True
