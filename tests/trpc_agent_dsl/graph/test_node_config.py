# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for common NodeConfig behavior."""

from trpc_agent_sdk.dsl.graph._node_config import NodeConfig


class TestNodeConfigCommonFields:
    """Tests for common config fields shared by all node types."""

    def test_config_preserves_common_fields(self):
        """NodeConfig should preserve name/description/metadata."""
        config = NodeConfig(
            name="worker",
            description="Does work",
            metadata={"owner": "team-a"},
        )

        assert config.name == "worker"
        assert config.description == "Does work"
        assert config.metadata == {"owner": "team-a"}


class TestNodeConfigMetadata:
    """Tests for metadata serialization logic."""

    def test_to_metadata_merges_custom_metadata_and_sets_node_type(self):
        """to_metadata should include common fields and explicit node type."""
        config = NodeConfig(
            name="Classifier",
            description="Classifies intent",
            metadata={
                "owner": "team-a",
                "version": "v2",
            },
        )

        metadata = config.to_metadata(node_type="llm")

        assert metadata["name"] == "Classifier"
        assert metadata["description"] == "Classifies intent"
        assert metadata["owner"] == "team-a"
        assert metadata["version"] == "v2"
        assert metadata["node_type"] == "llm"

    def test_to_metadata_node_type_overrides_custom_node_type_key(self):
        """Runtime node type should win even if metadata contains node_type."""
        config = NodeConfig(
            name="ToolExec",
            metadata={
                "node_type": "custom",
                "x": 1,
            },
        )

        metadata = config.to_metadata(node_type="tool")

        assert metadata["node_type"] == "tool"
        assert metadata["x"] == 1
