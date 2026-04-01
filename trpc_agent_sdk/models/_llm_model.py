# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Base model interface module.

This module defines the abstract base class (BaseModel) that serves as the foundation
for all model implementations in the system. It specifies the required interface and
common functionality that concrete model classes must implement.
"""

from abc import abstractmethod
from functools import partial
from typing import AsyncGenerator
from typing import List
from typing import Optional
from typing import final

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.context import create_agent_context
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import FilterRunner
from trpc_agent_sdk.filter import FilterType

from . import _constants as const
from ._llm_request import LlmRequest
from ._llm_response import LlmResponse


class LLMModel(FilterRunner):
    """Abstract base class for all model implementations."""

    def __init__(self, model_name: str, filters_name: Optional[list[str]] = None, **kwargs):
        filters: list = kwargs.get("filters", [])
        super().__init__(filters_name=filters_name, filters=filters)
        self._model_name = model_name
        self.config = kwargs
        self._type = FilterType.MODEL
        self._init_filters()
        self._api_key: str = kwargs.get(const.API_KEY, "")
        self._base_url: str = kwargs.get(const.BASE_URL, "")

    def set_api_key(self, value: str) -> None:
        """Set the API key."""
        self._api_key = value

    def set_base_url(self, value: str) -> None:
        """Set the base URL."""
        self._base_url = value

    def set_model_name(self, value: str) -> None:
        """Set the model name."""
        self._model_name = value

    @classmethod
    @abstractmethod
    def supported_models(cls) -> List[str]:
        """Return list of supported model name patterns (regex)."""

    @final
    async def generate_async(self,
                             request: LlmRequest,
                             stream: bool = False,
                             ctx: InvocationContext | None = None) -> AsyncGenerator[LlmResponse, None]:
        """Generate content asynchronously.

        Args:
            request: The LLM request
            stream: Whether to stream the response

        Yields:
            LlmResponse objects. For non-streaming, yields one response.
            For streaming, yields multiple partial responses.
            Error responses should have error_code and error_message set.
        """
        handle = partial(self._generate_async_impl, request, stream, ctx)  # type: ignore
        extra_filters: list[BaseFilter] = []
        if ctx:
            agent_context = ctx.agent_context
            before_model_callback = getattr(ctx.agent, "before_model_callback", None)
            after_model_callback = getattr(ctx.agent, "after_model_callback", None)
            from trpc_agent_sdk.agents import ModelCallbackFilter
            extra_filters.append(ModelCallbackFilter(before_model_callback, after_model_callback))
        else:
            agent_context = create_agent_context()

        async for event in self._run_stream_filters(agent_context, request, handle, extra_filters):  # type: ignore
            yield event  # type: ignore

    @abstractmethod
    async def _generate_async_impl(self,
                                   request: LlmRequest,
                                   stream: bool = False,
                                   ctx: InvocationContext | None = None) -> AsyncGenerator[LlmResponse, None]:
        """Generate content asynchronously.

        Args:
            ctx: The invocation context
            request: The LLM request
            stream: Whether to stream the response

        Yields:
            LlmResponse objects. For non-streaming, yields one response.
            For streaming, yields multiple partial responses.
            Error responses should have error_code and error_message set.
        """

    @abstractmethod
    def validate_request(self, request: LlmRequest) -> None:
        """Validate the request before processing.

        This method should check that the request is properly formed
        and contains all required fields for this model implementation.

        Args:
            request: The LLM request to validate

        Raises:
            ValueError: If request is invalid
        """

    @property
    def name(self) -> str:
        """Get the model name."""
        return self._model_name

    @property
    def display_name(self) -> str:
        """Get the display name for this model implementation."""
        return getattr(self.__class__, "_model_display_name", self.__class__.__name__)
