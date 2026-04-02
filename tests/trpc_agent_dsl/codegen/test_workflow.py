# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for codegen workflow loading and validation."""

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("trpc_agent_sdk.dsl.codegen", reason="trpc_agent_sdk.dsl.codegen not yet implemented")

from trpc_agent_sdk.dsl.codegen import load_workflow_definition


def _build_workflow_payload() -> dict[str, Any]:
    return {
        "name":
        "support_flow",
        "description":
        "Support workflow for parser tests.",
        "version":
        "1.0",
        "start_node_id":
        "start",
        "state_variables": [
            {
                "name": "ticket_id",
                "kind": "string"
            },
        ],
        "nodes": [
            {
                "id": "start",
                "node_type": "builtin.start",
                "config": {}
            },
            {
                "id": "agent",
                "label": "Support Agent",
                "node_type": "builtin.llmagent",
                "config": {
                    "model_spec": {
                        "provider": "openai",
                        "model_name": "env:OPENAI_MODEL",
                        "base_url": "env:OPENAI_BASE_URL",
                        "api_key": "env:OPENAI_API_KEY",
                        "headers": {
                            "x-trace-id": "trace-1"
                        },
                    },
                    "instruction":
                    "Classify issue and extract ticket id.",
                    "description":
                    "Support agent node",
                    "user_message":
                    "Input={{ state.user_input }}",
                    "temperature":
                    0.1,
                    "max_tokens":
                    256,
                    "top_p":
                    0.9,
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
                            "type": "mcp",
                            "name": "calculator",
                            "server_url": "https://mcp.example.com",
                            "transport": "sse",
                            "timeout": 12,
                            "headers": {
                                "x-service": "calc"
                            },
                            "allowed_tools": ["add", "sub"],
                        },
                        {
                            "type": "memory_search"
                        },
                        {
                            "type": "knowledge_search",
                            "name": "kb_search",
                            "description": "Search knowledge base",
                            "connector": {
                                "type": "trag",
                                "endpoint": "env:TRAG_ENDPOINT",
                                "token": "env:TRAG_TOKEN",
                                "rag_code": "env:TRAG_RAG_CODE",
                                "namespace": "env:TRAG_NAMESPACE",
                                "collection": "env:TRAG_COLLECTION",
                            },
                            "max_results": 7,
                            "min_score": 0.2,
                            "conditioned_filter": {
                                "status": "active"
                            },
                            "agentic_filter_info": {
                                "metadata.category": {
                                    "values": ["guide", "api"],
                                    "description": "Allowed categories",
                                },
                            },
                        },
                    ],
                    "skills": {
                        "roots": ["skills"],
                        "load_mode": "session",
                    },
                    "executor": {
                        "type": "local",
                        "work_dir": "workspace",
                        "workspace_mode": "shared",
                    },
                    "stream":
                    True,
                },
            },
            {
                "id": "set_state",
                "node_type": "builtin.set_state",
                "config": {
                    "assignments": [
                        {
                            "field": "ticket_id",
                            "expr": {
                                "expression": "input.output_parsed.ticket",
                                "format": "cel",
                            },
                        },
                    ],
                },
            },
            {
                "id": "approval",
                "node_type": "builtin.user_approval",
                "config": {
                    "message": "Approve this action?",
                    "routing": {
                        "approve": "end",
                        "reject": "__end__",
                    },
                },
            },
            {
                "id": "end",
                "node_type": "builtin.end",
                "config": {
                    "expr": {
                        "expression": "state.ticket_id",
                        "format": "cel",
                    }
                },
            },
        ],
        "edges": [
            {
                "id": "edge_start_agent",
                "source": "start",
                "target": "agent"
            },
            {
                "id": "edge_agent_set_state",
                "source": "agent",
                "target": "set_state"
            },
            {
                "id": "edge_set_state_approval",
                "source": "set_state",
                "target": "approval"
            },
        ],
        "conditional_edges": [],
    }


def _build_minimal_payload() -> dict[str, Any]:
    return {
        "name":
        "wrapped_flow",
        "description":
        "",
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
                "id": "end",
                "node_type": "builtin.end",
                "config": {}
            },
        ],
        "edges": [
            {
                "source": "start",
                "target": "end"
            },
        ],
        "conditional_edges": [],
    }


def _write_workflow(path: Path, payload: dict[str, Any]) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


class TestLoadWorkflowDefinition:
    """Tests for public workflow JSON loading behavior."""

    def test_loads_workflow_and_parses_node_configs(self, tmp_path: Path):
        """load_workflow_definition should parse typed config fields correctly."""
        workflow_path = _write_workflow(tmp_path / "workflow.json", _build_workflow_payload())

        workflow = load_workflow_definition(workflow_path)
        nodes_by_id = {node.node_id: node for node in workflow.nodes}

        assert workflow.name == "support_flow"
        assert workflow.start_node_id == "start"
        assert workflow.state_variables[0].name == "ticket_id"

        agent = nodes_by_id["agent"]
        assert agent.llm_config is not None
        assert agent.llm_config.model_spec.provider == "openai"
        assert agent.llm_config.model_spec.headers == {"x-trace-id": "trace-1"}
        assert agent.llm_config.output_mode == "json"
        assert agent.llm_config.output_schema is not None
        assert agent.llm_config.output_schema["properties"]["ticket"]["type"] == "string"
        assert len(agent.llm_config.mcp_tools) == 1
        assert agent.llm_config.mcp_tools[0].allowed_tools == ("add", "sub")
        assert len(agent.llm_config.memory_search_tools) == 1
        assert len(agent.llm_config.knowledge_search_tools) == 1
        assert agent.llm_config.knowledge_search_tools[0].knowledge_filter == {"status": "active"}
        assert (agent.llm_config.knowledge_search_tools[0].agentic_filter_info["metadata.category"].values == ("guide",
                                                                                                               "api"))
        assert agent.llm_config.skills is not None
        assert agent.llm_config.skills.roots == ("skills", )
        assert agent.llm_config.executor is not None
        assert agent.llm_config.executor.work_dir == "workspace"
        assert agent.llm_config.stream is True

        set_state = nodes_by_id["set_state"]
        assert set_state.set_state_config is not None
        assert set_state.set_state_config.assignments[0].field == "ticket_id"
        assert set_state.set_state_config.assignments[0].expr.expression == "input.output_parsed.ticket"

        approval = nodes_by_id["approval"]
        assert approval.user_approval_config is not None
        assert approval.user_approval_config.routing.approve == "end"
        assert approval.user_approval_config.routing.reject == "__end__"

    def test_supports_wrapped_root_graph_shape(self, tmp_path: Path):
        """Loader should accept payload.root.graph wrapped JSON shape."""
        workflow_path = _write_workflow(
            tmp_path / "wrapped.json",
            {"root": {
                "graph": _build_minimal_payload()
            }},
        )

        workflow = load_workflow_definition(workflow_path)
        assert workflow.name == "wrapped_flow"
        assert workflow.start_node_id == "start"
        assert len(workflow.nodes) == 2
        assert len(workflow.edges) == 1

    def test_rejects_non_openai_llm_provider(self, tmp_path: Path):
        """Validation should reject unsupported llmagent providers."""
        payload = _build_workflow_payload()
        payload["nodes"][1]["config"]["model_spec"]["provider"] = "anthropic"
        workflow_path = _write_workflow(tmp_path / "bad_provider.json", payload)

        with pytest.raises(ValueError, match="only openai"):
            load_workflow_definition(workflow_path)

    def test_rejects_user_approval_node_with_mismatched_direct_outgoing_edge(self, tmp_path: Path):
        """Mismatched direct approval edges should still fail validation."""
        payload = _build_workflow_payload()
        payload["edges"].append({
            "source": "approval",
            "target": "end",
        })
        workflow_path = _write_workflow(tmp_path / "bad_approval.json", payload)

        with pytest.raises(ValueError, match="do not match config.routing targets"):
            load_workflow_definition(workflow_path)

    def test_normalizes_user_approval_direct_edges_when_targets_match_routing(self, tmp_path: Path):
        """Direct approval edges matching config.routing should be normalized away."""
        payload = _build_workflow_payload()
        payload["edges"].extend([
            {
                "source": "approval",
                "target": "end",
            },
            {
                "source": "approval",
                "target": "__end__",
            },
        ])
        workflow_path = _write_workflow(tmp_path / "normalized_approval.json", payload)

        workflow = load_workflow_definition(workflow_path)

        assert all(edge.source != "approval" for edge in workflow.edges)
        assert len(workflow.edges) == 3

    def test_accepts_unknown_knowledge_connector_type_without_failing_parse(self, tmp_path: Path):
        """Unknown knowledge connector type should be preserved for render-time skip logic."""
        payload = _build_workflow_payload()
        payload["nodes"][1]["config"]["tools"][2]["connector"]["type"] = "iwiki"
        workflow_path = _write_workflow(tmp_path / "iwiki_connector.json", payload)

        workflow = load_workflow_definition(workflow_path)
        agent = next(node for node in workflow.nodes if node.node_id == "agent")
        assert agent.llm_config is not None
        assert agent.llm_config.knowledge_search_tools[0].connector.connector_type == "iwiki"
