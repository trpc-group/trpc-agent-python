# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""State schema for generated graph workflow."""

from typing import Literal

from pydantic import BaseModel
from trpc_agent_sdk.dsl.graph import State


class Llmagent1OutputModel(BaseModel):
    classification: Literal['return_item', 'cancel_subscription', 'get_information']


class WorkflowState(State):
    pass
