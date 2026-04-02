# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for codegen render entrypoint behavior."""

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("trpc_agent_sdk.dsl.codegen", reason="trpc_agent_sdk.dsl.codegen not yet implemented")

from trpc_agent_sdk.dsl.codegen import render_workflow_files
from trpc_agent_sdk.dsl.codegen import WorkflowDefinition
from trpc_agent_sdk.dsl.codegen import load_workflow_definition


def _build_render_payload() -> dict[str, Any]:
    return {
        "name":
        "render_flow",
        "description":
        "Render workflow for tests.",
        "version":
        "1.0",
        "start_node_id":
        "start",
        "nodes": [
            {
                "id": "start",
                "node_type": "builtin.start",
                "config": {}
            },
            {
                "id": "agent",
                "label": "Ticket Agent",
                "node_type": "builtin.llmagent",
                "config": {
                    "model_spec": {
                        "provider": "openai",
                        "model_name": "env:OPENAI_MODEL",
                        "base_url": "env:OPENAI_BASE_URL",
                        "api_key": "env:OPENAI_API_KEY",
                    },
                    "instruction":
                    "Process ticket for {{ state.user_input }}.",
                    "user_message":
                    "Ticket request: {{ state.user_input }}",
                    "output_format": {
                        "type": "json",
                        "schema": {
                            "type": "object",
                            "properties": {
                                "ticket": {
                                    "type": "string"
                                },
                            },
                            "required": ["ticket"],
                            "additionalProperties": False,
                        },
                    },
                    "tools": [
                        {
                            "type": "knowledge_search",
                            "name": "kb_tool",
                            "description": "Search docs",
                            "connector": {
                                "type": "trag",
                                "endpoint": "env:TRAG_ENDPOINT",
                                "token": "env:TRAG_TOKEN",
                                "rag_code": "env:TRAG_RAG_CODE",
                                "namespace": "env:TRAG_NAMESPACE",
                                "collection": "env:TRAG_COLLECTION",
                            },
                        },
                    ],
                },
            },
            {
                "id": "transform",
                "node_type": "builtin.transform",
                "config": {
                    "expr": {
                        "expression": "{ \"ticket\": input.output_parsed.ticket, "
                        "\"is_billing\": state.user_input.contains('bill') }",
                        "format": "cel",
                    },
                },
            },
            {
                "id": "end",
                "node_type": "builtin.end",
                "config": {
                    "expr": {
                        "expression": "nodes.transform.output_parsed",
                        "format": "cel",
                    },
                },
            },
        ],
        "edges": [
            {
                "source": "start",
                "target": "agent"
            },
            {
                "source": "agent",
                "target": "transform"
            },
            {
                "source": "transform",
                "target": "end"
            },
        ],
        "conditional_edges": [],
    }


def _build_render_payload_with_unknown_llm_knowledge_connector() -> dict[str, Any]:
    payload = _build_render_payload()
    payload["name"] = "render_flow_unknown_llm_knowledge_connector"
    payload["nodes"][1]["config"]["tools"][0]["connector"]["type"] = "iwiki"
    return payload


def _build_render_payload_with_unsupported_knowledge_node_connector() -> dict[str, Any]:
    return {
        "name":
        "render_flow_unknown_knowledge_node_connector",
        "description":
        "Knowledge node connector type is not implemented.",
        "version":
        "1.0",
        "start_node_id":
        "start",
        "nodes": [
            {
                "id": "start",
                "node_type": "builtin.start",
                "config": {}
            },
            {
                "id": "knowledge_node",
                "node_type": "builtin.knowledge_search",
                "config": {
                    "query": "Find docs",
                    "connector": {
                        "type": "iwiki",
                        "endpoint": "https://knowledge.example.com",
                    },
                },
            },
            {
                "id": "end",
                "node_type": "builtin.end",
                "config": {}
            },
        ],
        "edges": [
            {
                "source": "start",
                "target": "knowledge_node"
            },
            {
                "source": "knowledge_node",
                "target": "end"
            },
        ],
        "conditional_edges": [],
    }


def _build_multi_upstream_mcp_payload() -> dict[str, Any]:
    return {
        "name":
        "invalid_mcp_sources",
        "description":
        "MCP node has two upstream nodes.",
        "version":
        "1.0",
        "start_node_id":
        "start",
        "nodes": [
            {
                "id": "start",
                "node_type": "builtin.start",
                "config": {}
            },
            {
                "id": "agent_a",
                "node_type": "builtin.llmagent",
                "config": {
                    "model_spec": {
                        "provider": "openai",
                        "model_name": "gpt-4o-mini",
                        "api_key": "env:OPENAI_API_KEY",
                    },
                    "instruction": "Agent A",
                },
            },
            {
                "id": "agent_b",
                "node_type": "builtin.llmagent",
                "config": {
                    "model_spec": {
                        "provider": "openai",
                        "model_name": "gpt-4o-mini",
                        "api_key": "env:OPENAI_API_KEY",
                    },
                    "instruction": "Agent B",
                },
            },
            {
                "id": "mcp_call",
                "node_type": "builtin.mcp",
                "config": {
                    "mcp": {
                        "server_url": "https://mcp.example.com",
                        "transport": "streamable_http",
                    },
                    "function": "add",
                },
            },
            {
                "id": "end",
                "node_type": "builtin.end",
                "config": {}
            },
        ],
        "edges": [
            {
                "source": "start",
                "target": "agent_a"
            },
            {
                "source": "start",
                "target": "agent_b"
            },
            {
                "source": "agent_a",
                "target": "mcp_call"
            },
            {
                "source": "agent_b",
                "target": "mcp_call"
            },
            {
                "source": "mcp_call",
                "target": "end"
            },
        ],
        "conditional_edges": [],
    }


def _load_workflow(tmp_path: Path, payload: dict[str, Any], file_name: str) -> WorkflowDefinition:
    workflow_path = tmp_path / file_name
    workflow_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return load_workflow_definition(workflow_path)


class TestRenderWorkflowFiles:
    """Tests for render_workflow_files() public output contract."""

    def test_renders_base_project_without_service_files(self, tmp_path: Path):
        """Default render should include local-runner files only."""
        workflow = _load_workflow(tmp_path, _build_render_payload(), "render_flow.json")
        rendered = render_workflow_files(workflow)

        assert set(rendered.keys()) == {
            ".env",
            "README.md",
            "agent/__init__.py",
            "agent/agent.py",
            "agent/callbacks.py",
            "agent/config.py",
            "agent/nodes.py",
            "agent/prompts.py",
            "agent/state.py",
            "agent/tools.py",
            "requirements.txt",
            "run_agent.py",
        }

        assert "class Llmagent1OutputModel(BaseModel):" in rendered["agent/state.py"]
        assert "async def node_transform1(state: WorkflowState)" in rendered["agent/nodes.py"]
        assert "state[STATE_KEY_NODE_RESPONSES][NODE_ID_LLMAGENT1]['ticket']" in rendered["agent/nodes.py"]
        assert "Process ticket for {user_input}." in rendered["agent/prompts.py"]

        dotenv_text = rendered[".env"]
        for env_entry in [
                "MODEL1_NAME=\"env:OPENAI_MODEL\"",
                "MODEL1_API_KEY=\"env:OPENAI_API_KEY\"",
                "MODEL1_BASE_URL=\"env:OPENAI_BASE_URL\"",
                "KNOWLEDGE1_TYPE=\"trag\"",
                "KNOWLEDGE1_ENDPOINT=\"env:TRAG_ENDPOINT\"",
                "KNOWLEDGE1_TOKEN=\"env:TRAG_TOKEN\"",
                "KNOWLEDGE1_RAG_CODE=\"env:TRAG_RAG_CODE\"",
                "KNOWLEDGE1_NAMESPACE=\"env:TRAG_NAMESPACE\"",
                "KNOWLEDGE1_COLLECTION=\"env:TRAG_COLLECTION\"",
        ]:
            assert env_entry in dotenv_text

    def test_skips_unsupported_llm_knowledge_connector_in_generated_tooling(self, tmp_path: Path):
        """Unsupported llmagent knowledge connector should be skipped, not fail render."""
        workflow = _load_workflow(
            tmp_path,
            _build_render_payload_with_unknown_llm_knowledge_connector(),
            "render_flow_unknown_llm_knowledge_connector.json",
        )
        rendered = render_workflow_files(workflow)

        assert "KNOWLEDGE1_TYPE" not in rendered[".env"]
        assert "create_tools_llmagent1" in rendered["agent/tools.py"]
        assert "tools: list[Any] = []" in rendered["agent/tools.py"]
        assert "knowledge_tool_1_auth_params" not in rendered["agent/tools.py"]

    def test_falls_back_to_noop_node_for_unsupported_knowledge_node_connector(self, tmp_path: Path):
        """Unsupported standalone knowledge node connector should become a no-op node."""
        workflow = _load_workflow(
            tmp_path,
            _build_render_payload_with_unsupported_knowledge_node_connector(),
            "render_flow_unknown_knowledge_node_connector.json",
        )
        rendered = render_workflow_files(workflow)

        assert "graph.add_knowledge_node(" not in rendered["agent/agent.py"]
        assert "NODE_ID_KNOWLEDGE_SEARCH1" in rendered["agent/agent.py"]
        assert "node_knowledge_search1" in rendered["agent/nodes.py"]
        assert "custom node logic for 'builtin.knowledge_search'" in rendered["agent/nodes.py"]

    def test_renders_http_service_scaffold_when_service_mode_is_http(self, tmp_path: Path):
        """HTTP mode should generate service entry files and service-specific docs."""
        workflow = _load_workflow(tmp_path, _build_render_payload(), "http_flow.json")
        rendered = render_workflow_files(workflow, service_mode="http")

        for file_name in [
                "trpc_main.py",
                "trpc_python.yaml",
                "_agent_runner.py",
                "http_service.py",
                "client.py",
        ]:
            assert file_name in rendered

        assert "Protocol: HTTP + SSE" in rendered["README.md"]
        assert "trpc_naming_polaris" in rendered["requirements.txt"]
        assert "a2a_service.py" not in rendered
        assert "agui_service.py" not in rendered

    def test_rejects_unknown_service_mode(self, tmp_path: Path):
        """Unsupported service mode should fail fast."""
        workflow = _load_workflow(tmp_path, _build_render_payload(), "invalid_service.json")
        with pytest.raises(ValueError, match="Unsupported service mode"):
            render_workflow_files(workflow, service_mode="grpc")

    def test_rejects_mcp_node_with_multiple_upstream_nodes(self, tmp_path: Path):
        """MCP node currently requires exactly one upstream source node."""
        workflow = _load_workflow(tmp_path, _build_multi_upstream_mcp_payload(), "multi_upstream_mcp.json")

        with pytest.raises(ValueError, match="requires exactly one upstream node"):
            render_workflow_files(workflow)
