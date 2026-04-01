# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Common node configuration for graph nodes.

NodeConfig only contains fields shared by all node kinds.
Node-specific behavior is configured at the corresponding add_*_node API.
"""

from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Optional


@dataclass
class NodeConfig:
    """Common configuration shared by function/llm/tool/agent nodes.

    Attributes:
        name: Human-readable name for the node.
        description: Description of what the node does.
        metadata: Arbitrary metadata dictionary.
    """

    name: Optional[str] = None
    description: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_metadata(self, *, node_type: str) -> dict[str, Any]:
        """Convert NodeConfig to graph metadata.

        Args:
            node_type: Node type string (function/llm/tool/agent).

        Returns:
            Metadata dictionary suitable for graph engine adapters.
        """
        result: dict[str, Any] = {}

        if self.name:
            result["name"] = self.name
        if self.description:
            result["description"] = self.description

        if self.metadata:
            result.update(self.metadata)

        result["node_type"] = node_type

        return result
