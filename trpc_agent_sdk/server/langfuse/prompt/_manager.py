# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Langfuse Remote Instruction Manager for tRPC-Agent.

Fetches instructions via Langfuse REST API and injects version info into OTel traces.
Zero SDK dependency — uses only HTTP requests and OTel span attributes.

API Reference: https://api.reference.langfuse.com/#tag/prompts/GET/api/public/v2/prompts/{promptName}
"""

from typing import Any
from typing import Dict
from typing import Optional
from urllib.parse import quote

import requests

from trpc_agent_sdk.log import logger
from trpc_agent_sdk.types import Instruction
from trpc_agent_sdk.types import InstructionMetadata


class RemoteInstructionManager:
    """Fetches instructions from Langfuse REST API.

    Uses ``GET /api/public/v2/prompts/{promptName}`` with Basic Auth.
    """

    def __init__(
        self,
        public_key: str,
        secret_key: str,
        host: str,
    ):
        self._host = host.rstrip("/")
        self._auth = (public_key, secret_key)
        logger.info("RemoteInstructionManager initialized (host=%s)", self._host)

    def get_instruction(
        self,
        name: str,
        version: Optional[int] = None,
        label: Optional[str] = None,
    ) -> Instruction:
        """Fetch an instruction from Langfuse.

        Instruction-to-generation trace association is automatic when
        the returned ``Instruction`` is passed as ``LlmAgent.instruction``.

        Args:
            name: Instruction name in Langfuse.
            version: Specific version number.
            label: Specific label such as ``"production"`` or ``"staging"``.
                   Defaults to ``"production"`` on the server side when neither
                   *version* nor *label* is provided.

        Returns:
            Instruction with template content and version metadata.
        """
        url = f"{self._host}/api/public/v2/prompts/{quote(name, safe='')}"
        params: Dict[str, Any] = {}
        if version is not None:
            params["version"] = version
        if label is not None:
            params["label"] = label

        try:
            resp = requests.get(url, params=params, auth=self._auth, timeout=10)
            resp.raise_for_status()
        except Exception as e:
            logger.error("Failed to fetch instruction '%s' from Langfuse: %s", name, e)
            raise

        data = resp.json()
        logger.debug("Fetched instruction '%s' from Langfuse: %s", name, data)
        metadata = InstructionMetadata(
            name=data["name"],
            version=data["version"],
            type=data.get("type", "text"),
            labels=data.get("labels", []),
            config=data.get("config", {}),
        )
        result = Instruction(
            instruction=data["prompt"],
            metadata=metadata,
        )

        logger.info(
            "Fetched instruction '%s' v%d (labels=%s)",
            metadata.name,
            metadata.version,
            metadata.labels,
        )
        return result
