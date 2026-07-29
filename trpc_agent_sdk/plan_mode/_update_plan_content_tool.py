# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.

from __future__ import annotations

import time
from typing import Any
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema
from trpc_agent_sdk.types import Type

from ._base import _PlanToolBase
from ._prompt import DEFAULT_UPDATE_CONTENT_DESCRIPTION
from ._store import apply_update_content


class UpdatePlanContentTool(_PlanToolBase):

    def __init__(self, *, name: str = "update_plan_content", **kwargs: Any) -> None:
        super().__init__(name=name, description=DEFAULT_UPDATE_CONTENT_DESCRIPTION, **kwargs)

    @override
    def _get_declaration(self) -> FunctionDeclaration:
        return FunctionDeclaration(
            name=self.name,
            description=DEFAULT_UPDATE_CONTENT_DESCRIPTION,
            parameters=Schema(
                type=Type.OBJECT,
                properties={
                    "content": Schema(type=Type.STRING, description="Markdown plan text."),
                    "mode": Schema(
                        type=Type.STRING,
                        description="'append' (default) or 'replace'.",
                    ),
                },
                required=["content"],
            ),
        )

    @override
    async def _run_plan(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        content = args.get("content")
        if not isinstance(content, str):
            return {"error": "INVALID_ARGS: `content` must be a string"}
        mode = args.get("mode") or "append"
        if not isinstance(mode, str):
            mode = "append"

        record, error = apply_update_content(
            self._load_plan(tool_context),
            content=content,
            mode=mode,
            now_unix=int(time.time()),
        )
        if error:
            return {"error": f"INVALID_STATE: {error}"}

        self._save_plan(tool_context, record)
        return {
            "message": f"Plan content updated ({mode}).",
            "plan": record.model_dump(mode="json", by_alias=True),
        }
