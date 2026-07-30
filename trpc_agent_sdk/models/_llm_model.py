# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
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

from trpc_agent_sdk.configs import ModelRetryConfig
from trpc_agent_sdk.configs import PromptCacheConfig
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.context import create_agent_context
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.filter import FilterRunner
from trpc_agent_sdk.filter import FilterType
from . import _constants as const
from ._llm_request import LlmRequest
from ._llm_response import LlmResponse
from ._retry import ModelRetryInfo
from ._retry import _model_retry_info_from_exception
from ._retry import retry_model_call

_VALID_ROLES: set[str] = {const.USER, const.ASSISTANT, const.MODEL, const.SYSTEM}


class LLMModel(FilterRunner):
    """Abstract base class for all model implementations."""

    def __init__(
        self,
        model_name: str,
        filters_name: Optional[list[str]] = None,
        prompt_cache_config: Optional[PromptCacheConfig] = None,
        model_retry_config: Optional[ModelRetryConfig] = None,
        **kwargs,
    ):
        filters: list = kwargs.get("filters", [])
        super().__init__(filters_name=filters_name, filters=filters)
        self._model_name = model_name
        self.config = kwargs
        self.prompt_cache_config = prompt_cache_config
        self.model_retry_config = model_retry_config
        self._type = FilterType.MODEL
        self._init_filters()
        self._api_key: str = kwargs.get(const.API_KEY, "")
        self._base_url: str = kwargs.get(const.BASE_URL, "")

    def _resolve_prompt_cache_config(
        self,
        ctx: Optional[InvocationContext] = None,
    ) -> Optional[PromptCacheConfig]:
        """Resolve the effective prompt cache config for a call.

        The model-level ``prompt_cache_config`` is the baseline; per-run
        ``RunConfig.prompt_cache`` (via ``ctx``) overrides it field-by-field,
        so a run can tweak just one field (e.g. ``cache_key``) without having to
        re-declare the rest. Returns the merged config only when it is enabled,
        otherwise ``None`` (callers treat ``None`` as "do nothing").
        """
        base = self.prompt_cache_config
        run = ctx.run_config.prompt_cache if (ctx is not None and ctx.run_config is not None) else None

        if run is None:
            config = base
        elif base is None:
            config = run
        else:
            # Only fields explicitly set on the per-run config override the baseline.
            config = base.model_copy(update=run.model_dump(exclude_unset=True))

        if config is None or not config.enabled:
            return None
        return config

    def set_api_key(self, value: str) -> None:
        """Set the API key."""
        self._api_key = value

    def set_base_url(self, value: str) -> None:
        """Set the base URL."""
        self._base_url = value

    def set_model_name(self, value: str) -> None:
        """Set the model name."""
        self._model_name = value

    def is_retriable_status_code(self, status_code: int) -> Optional[bool]:
        """Map an HTTP status code to a retry decision.

        Return ``True``/``False`` for codes the provider has an opinion on, or
        ``None`` to defer to :meth:`is_retriable_exception`.
        """
        return None

    def is_retriable_exception(self, ex: Exception) -> bool:
        """Fallback retry decision when no status code or header hint applies."""
        return False

    @final
    def _get_model_retry_info(self, ex: Exception) -> ModelRetryInfo:
        """Build retry info from headers/status, then the provider hooks.

        Providers customize behavior by overriding :meth:`is_retriable_status_code`
        and :meth:`is_retriable_exception`, never this orchestration method.
        """
        return _model_retry_info_from_exception(
            ex,
            self.is_retriable_status_code,
            self.is_retriable_exception,
        )

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
        call_model = partial(self._generate_async_impl, request, stream, ctx)  # type: ignore
        error_code = "STREAMING_ERROR" if stream else "API_ERROR"
        run_with_retry = partial(
            retry_model_call,
            call_model,
            self.model_retry_config,
            error_code=error_code,
            get_retry_info=self._get_model_retry_info,
        )
        extra_filters: list[BaseFilter] = []
        if ctx:
            agent_context = ctx.agent_context
            before_model_callback = getattr(ctx.agent, "before_model_callback", None)
            after_model_callback = getattr(ctx.agent, "after_model_callback", None)
            from trpc_agent_sdk.agents import ModelCallbackFilter
            extra_filters.append(ModelCallbackFilter(before_model_callback, after_model_callback))
        else:
            agent_context = create_agent_context()

        async for event in self._run_stream_filters(agent_context, request, run_with_retry,
                                                    extra_filters):  # type: ignore
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

    def validate_request(self, request: LlmRequest) -> None:
        """Validate the request before processing.

        This method should check that the request is properly formed
        and contains all required fields for this model implementation.

        Args:
            request: The LLM request to validate

        Raises:
            ValueError: If request is invalid
        """
        if not request.contents:
            raise ValueError("At least one content is required")

        # Validate content structure
        for content in request.contents:
            if not content.parts:
                raise ValueError("Content must have at least one part")

            # Check if content has valid role
            if content.role and content.role not in _VALID_ROLES:
                raise ValueError(f"Invalid content role: {content.role}")

    @property
    def name(self) -> str:
        """Get the model name."""
        return self._model_name

    @property
    def display_name(self) -> str:
        """Get the display name for this model implementation."""
        return getattr(self.__class__, "_model_display_name", self.__class__.__name__)
