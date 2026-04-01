# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Memory types."""

from __future__ import annotations

from typing import Optional

from google.genai.types import Content
from pydantic import BaseModel
from pydantic import Field


class MemoryEntry(BaseModel):
    """Represent one memory entry."""

    content: Content
    """The main content of the memory."""

    author: Optional[str] = None
    """The author of the memory."""

    timestamp: Optional[str] = None
    """The timestamp when the original content of this memory happened.

    This string will be forwarded to LLM. Preferred format is ISO 8601 format.
    """


class SearchMemoryResponse(BaseModel):
    """Represents the response from a memory search.

    Attributes:
        memories: A list of memory entries that relate to the search query.
    """

    memories: list[MemoryEntry] = Field(default_factory=list)
