# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Session data structure."""

from __future__ import annotations

import time
from typing import Any
from typing import Dict

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import alias_generators


class SessionABC(BaseModel):
    """Represents a series of interactions between a user and agents.

    This class manages the state and events of a conversation session,
    providing methods to add events, update state, and track session metadata.

    Attributes:
        id: The unique identifier of the session.
        app_name: The name of the application.
        user_id: The id of the user.
        state: The state of the session as a dictionary.
        events: The events of the session, e.g. user input, model response,
                function call/response, etc.
        last_update_time: The last update time as a float timestamp.
    """

    model_config = ConfigDict(
        extra='forbid',
        arbitrary_types_allowed=True,
        alias_generator=alias_generators.to_camel,
        populate_by_name=True,
    )
    """The pydantic model config."""

    id: str = Field(..., description="The unique identifier of the session")
    """The unique identifier of the session."""

    app_name: str = Field(..., description="The name of the application")
    """The name of the application."""

    user_id: str = Field(..., description="The id of the user")
    """The id of the user."""

    state: Dict[str, Any] = Field(default_factory=dict, description="The state of the session")
    """The state of the session."""

    last_update_time: float = Field(default_factory=time.time, description="The last update time as a float timestamp")
    """The last update time as a float timestamp."""

    save_key: str = Field(..., description="The key to save the session")
    """The key to save the session."""

    conversation_count: int = Field(default=0, description="The count of the conversation")
    """The count of the conversation."""
