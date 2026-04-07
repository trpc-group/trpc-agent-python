"""Unit tests for trpc_agent_sdk.server.openclaw.agent._agent."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trpc_agent_sdk.server.openclaw.agent._agent import (
    create_agent,
    create_model,
    create_worker_agent,
)


def _make_config(workspace, **overrides):
    defaults = dict(
        model_api_key="test-key",
        model_base_url="http://localhost:8000",
        model_name="gpt-4",
        restrict_to_workspace=True,
        temperature=0.7,
        max_tokens=4096,
    )
    defaults.update(overrides)

    config = MagicMock()
    config.workspace = workspace
    config.model_api_key = defaults["model_api_key"]
    config.model_base_url = defaults["model_base_url"]
    config.model_name = defaults["model_name"]
    config.model_extra_headers = {}
    config.agent = MagicMock()
    config.agent.temperature = defaults["temperature"]
    config.agent.max_tokens = defaults["max_tokens"]
    config.agent.instruction = ""
    config.agent.system_prompt = ""
    config.tools = MagicMock()
    config.tools.restrict_to_workspace = defaults["restrict_to_workspace"]
    config.tools.web = MagicMock()
    config.tools.web.search = MagicMock()
    config.tools.web.proxy = None
    config.tools.mcp_servers = []
    config.personal = []
    return config


class TestCreateModel:

    def test_missing_api_key_raises(self, tmp_path):
        config = _make_config(tmp_path, model_api_key="")
        with pytest.raises(ValueError, match="Model config missing"):
            create_model(config)

    def test_missing_base_url_raises(self, tmp_path):
        config = _make_config(tmp_path, model_base_url="")
        with pytest.raises(ValueError, match="Model config missing"):
            create_model(config)

    def test_missing_model_name_raises(self, tmp_path):
        config = _make_config(tmp_path, model_name="")
        with pytest.raises(ValueError, match="Model config missing"):
            create_model(config)

    @patch("trpc_agent_sdk.server.openclaw.agent._agent.OpenAIModel")
    def test_success(self, mock_openai_model, tmp_path):
        mock_openai_model.return_value = MagicMock()
        config = _make_config(tmp_path)
        model = create_model(config)

        mock_openai_model.assert_called_once_with(
            model_name="gpt-4",
            api_key="test-key",
            base_url="http://localhost:8000",
        )
        assert model is mock_openai_model.return_value


class TestCreateWorkerAgent:

    @patch("trpc_agent_sdk.server.openclaw.agent._agent.preload_memory_tool", new_callable=lambda: MagicMock)
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.WebFetchTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.WebSearchTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.ExecTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.ListDirTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.EditFileTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.WriteFileTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.ReadFileTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.LlmAgent")
    def test_returns_agent_with_tools(
        self,
        mock_llm_agent,
        mock_read,
        mock_write,
        mock_edit,
        mock_listdir,
        mock_exec,
        mock_web_search,
        mock_web_fetch,
        mock_preload,
        tmp_path,
    ):
        config = _make_config(tmp_path)
        model = MagicMock()
        mock_llm_agent.return_value = MagicMock()

        result = create_worker_agent(config, model)

        mock_llm_agent.assert_called_once()
        call_kwargs = mock_llm_agent.call_args
        assert call_kwargs[1]["name"] == "trpc-claw-py_worker"
        assert len(call_kwargs[1]["tools"]) == 8
        assert result is mock_llm_agent.return_value


class TestCreateAgent:

    @patch("trpc_agent_sdk.server.openclaw.agent._agent.build_mcp_toolsets", return_value=[])
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.create_skill_tool_set")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.ClawPrompts")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.preload_memory_tool", new_callable=lambda: MagicMock)
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.SpawnTaskTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.MessageTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.WebFetchTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.WebSearchTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.ExecTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.ListDirTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.EditFileTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.WriteFileTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.ReadFileTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.LlmAgent")
    def test_returns_agent_with_tools(
        self,
        mock_llm_agent,
        mock_read,
        mock_write,
        mock_edit,
        mock_listdir,
        mock_exec,
        mock_web_search,
        mock_web_fetch,
        mock_message,
        mock_spawn,
        mock_preload,
        mock_prompts_cls,
        mock_skill_tool_set,
        mock_build_mcp,
        tmp_path,
    ):
        config = _make_config(tmp_path)
        model = MagicMock()
        worker = MagicMock()

        mock_prompts_instance = MagicMock()
        mock_prompts_instance.build_system_prompt.return_value = "system prompt"
        mock_prompts_cls.return_value = mock_prompts_instance

        mock_skill_ts = MagicMock()
        mock_skill_ts.repository = MagicMock()
        mock_skill_tool_set.return_value = mock_skill_ts

        mock_llm_agent.return_value = MagicMock()

        result = create_agent(config, model, worker_agent=worker)

        mock_llm_agent.assert_called_once()
        call_kwargs = mock_llm_agent.call_args[1]
        assert call_kwargs["name"] == "trpc-claw-py"
        assert call_kwargs["sub_agents"] == [worker]
        assert call_kwargs["skill_repository"] is mock_skill_ts.repository
        assert result is mock_llm_agent.return_value

    @patch("trpc_agent_sdk.server.openclaw.agent._agent.build_mcp_toolsets", return_value=[])
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.create_skill_tool_set")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.ClawPrompts")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.preload_memory_tool", new_callable=lambda: MagicMock)
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.SpawnTaskTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.MessageTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.WebFetchTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.WebSearchTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.ExecTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.ListDirTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.EditFileTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.WriteFileTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.ReadFileTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.LlmAgent")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.CronTool")
    def test_with_cron_service(
        self,
        mock_cron_tool,
        mock_llm_agent,
        mock_read,
        mock_write,
        mock_edit,
        mock_listdir,
        mock_exec,
        mock_web_search,
        mock_web_fetch,
        mock_message,
        mock_spawn,
        mock_preload,
        mock_prompts_cls,
        mock_skill_tool_set,
        mock_build_mcp,
        tmp_path,
    ):
        config = _make_config(tmp_path)
        model = MagicMock()
        cron = MagicMock()
        worker = MagicMock()

        mock_prompts_instance = MagicMock()
        mock_prompts_instance.build_system_prompt.return_value = "prompt"
        mock_prompts_cls.return_value = mock_prompts_instance

        mock_skill_ts = MagicMock()
        mock_skill_ts.repository = MagicMock()
        mock_skill_tool_set.return_value = mock_skill_ts

        mock_llm_agent.return_value = MagicMock()

        create_agent(config, model, cron_service=cron, worker_agent=worker)

        mock_cron_tool.assert_called_once_with(cron_service=cron)

    @patch("trpc_agent_sdk.server.openclaw.agent._agent.build_mcp_toolsets", return_value=[])
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.create_skill_tool_set")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.ClawPrompts")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.preload_memory_tool", new_callable=lambda: MagicMock)
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.SpawnTaskTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.MessageTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.WebFetchTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.WebSearchTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.ExecTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.ListDirTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.EditFileTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.WriteFileTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.ReadFileTool")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.LlmAgent")
    @patch("trpc_agent_sdk.server.openclaw.agent._agent.create_worker_agent")
    def test_creates_default_worker_when_none_provided(
        self,
        mock_create_worker,
        mock_llm_agent,
        mock_read,
        mock_write,
        mock_edit,
        mock_listdir,
        mock_exec,
        mock_web_search,
        mock_web_fetch,
        mock_message,
        mock_spawn,
        mock_preload,
        mock_prompts_cls,
        mock_skill_tool_set,
        mock_build_mcp,
        tmp_path,
    ):
        config = _make_config(tmp_path)
        model = MagicMock()

        mock_prompts_instance = MagicMock()
        mock_prompts_instance.build_system_prompt.return_value = "prompt"
        mock_prompts_cls.return_value = mock_prompts_instance

        mock_skill_ts = MagicMock()
        mock_skill_ts.repository = MagicMock()
        mock_skill_tool_set.return_value = mock_skill_ts

        mock_llm_agent.return_value = MagicMock()
        mock_create_worker.return_value = MagicMock()

        create_agent(config, model)

        mock_create_worker.assert_called_once()
