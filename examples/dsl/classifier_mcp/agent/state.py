# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""State schema for generated graph workflow."""

from typing import Literal

from pydantic import BaseModel
from trpc_agent_sdk.dsl.graph import State


class Llmagent1OutputModel(BaseModel):
    classification: Literal['math_simple', 'math_complex']
    reason: str


class WorkflowState(State):
    pass
