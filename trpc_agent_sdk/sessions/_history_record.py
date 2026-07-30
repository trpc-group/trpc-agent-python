# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""History record for TRPC Agent framework."""

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


class HistoryRecord(BaseModel):
    """History record"""
    model_config = ConfigDict(
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    user_texts: list[str] = Field(default_factory=list, description="List of user text")
    # The text of the user
    assistant_texts: list[str] = Field(default_factory=list, description="List of assistant text")

    # The text of the assistant

    def add_record(self, user_text: str, assistant_text: str | None = ""):
        """Add a record"""
        if not user_text and assistant_text:
            raise ValueError("when user text is empty, assistant text must be empty")

        if not user_text.startswith("user:"):
            user_text = f"user: {user_text}"
        self.user_texts.append(user_text)

        if not assistant_text:
            return

        if not assistant_text.startswith("assistant:"):
            assistant_text = f"assistant: {assistant_text}"
        self.assistant_texts.append(assistant_text)

    def build_content(self, user_message: str = "") -> Content:
        """Build the Content"""
        if len(self.user_texts) < len(self.assistant_texts):
            raise ValueError("user texts must more than assistant texts")
        parts: list[Part] = []
        index = 0
        for assistant_text in self.assistant_texts:
            parts.append(Part(text=self.user_texts[index]))
            parts.append(Part(text=assistant_text))
            index = index + 1
        for user_text in self.user_texts[index:]:
            parts.append(Part(text=user_text))
        parts.append(Part(text=user_message))
        return Content(parts=parts, role="user")
