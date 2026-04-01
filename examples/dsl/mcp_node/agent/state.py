# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""State schema for generated graph workflow."""

from pydantic import BaseModel
from pydantic import ConfigDict
from trpc_agent_sdk.dsl.graph import State


class Llmagent1OutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    a: int
    b: int


class WorkflowState(State):
    pass
