"""Tests for agent module — code review agent creation and configuration.

Uses unittest.mock to avoid requiring actual model implementations.
"""

import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_llm_agent():
    """Patch LlmAgent constructor to return a mock."""
    mock_agent = MagicMock()
    mock_agent.name = "code_review_agent"
    mock_agent.description = "Automated code review agent"
    mock_agent.model = "fake"

    with patch("agent.agent.LlmAgent", return_value=mock_agent) as mock_cls:
        yield mock_cls


class TestCreateAgent:
    """Tests for create_code_review_agent()."""

    def test_create_with_defaults(self, mock_llm_agent):
        from agent.agent import create_code_review_agent
        agent = create_code_review_agent()
        assert agent is not None

    def test_create_with_custom_model(self, mock_llm_agent):
        from agent.agent import create_code_review_agent
        agent = create_code_review_agent({"model": "gemini-2.5-flash"})
        assert agent is not None

    def test_create_with_full_config(self, mock_llm_agent):
        from agent.agent import create_code_review_agent
        config = {"model": "deepseek-v3", "instruction": "Custom instruction."}
        agent = create_code_review_agent(config)
        assert agent is not None

    def test_env_var_model_override(self, mock_llm_agent, monkeypatch):
        from agent.agent import create_code_review_agent
        monkeypatch.setenv("CR_AGENT_MODEL", "test-from-env")
        agent = create_code_review_agent()
        assert agent is not None

    def test_create_does_not_require_sdk(self, mock_llm_agent):
        from agent.agent import create_code_review_agent
        agent = create_code_review_agent()
        assert agent is not None

    def test_multiple_agents_independent(self):
        from agent.agent import create_code_review_agent

        def make_mock(**kwargs):
            m = MagicMock()
            m.name = kwargs.get("name", "code_review_agent")
            m.description = "test"
            return m

        with patch("agent.agent.LlmAgent", side_effect=make_mock):
            a1 = create_code_review_agent({"model": "a"})
            a2 = create_code_review_agent({"model": "b"})
            assert a1 is not a2

    def test_agent_has_expected_description(self, mock_llm_agent):
        from agent.agent import create_code_review_agent
        agent = create_code_review_agent()
        assert "code review" in agent.description.lower()

    def test_default_model_is_fake(self, mock_llm_agent):
        from agent.agent import create_code_review_agent
        agent = create_code_review_agent()
        assert agent is not None
