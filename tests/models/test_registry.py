# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

from typing import AsyncGenerator
from typing import List

import pytest
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.models import ModelRegistry
from trpc_agent_sdk.models import register_model


class TestModelBase(LLMModel):
    """Base test model for testing."""

    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"test-.*"]

    async def _generate_async_impl(self,
                                   request: LlmRequest,
                                   stream: bool = False,
                                   ctx: InvocationContext | None = None) -> AsyncGenerator[LlmResponse, None]:
        """Test implementation."""
        yield LlmResponse(content=None)

    def validate_request(self, request: LlmRequest) -> None:
        """Test validation."""
        pass


class AnotherTestModel(LLMModel):
    """Another test model."""

    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"another-.*"]

    async def _generate_async_impl(self,
                                   request: LlmRequest,
                                   stream: bool = False,
                                   ctx: InvocationContext | None = None) -> AsyncGenerator[LlmResponse, None]:
        """Test implementation."""
        yield LlmResponse(content=None)

    def validate_request(self, request: LlmRequest) -> None:
        """Test validation."""
        pass


class TestModelRegistry:
    """Test suite for ModelRegistry class."""

    def setup_method(self):
        """Set up test fixtures before each test."""
        # Save original registry
        self.original_registry = ModelRegistry._registry.copy()
        # Clear registry for testing
        ModelRegistry._registry.clear()

    def teardown_method(self):
        """Clean up after each test."""
        # Restore original registry
        ModelRegistry._registry = self.original_registry

    def test_register_classes(self):
        """Test registering model classes."""
        ModelRegistry.register(TestModelBase)
        ModelRegistry.register(AnotherTestModel)

        # Verify both models are registered
        assert r"test-.*" in ModelRegistry._registry
        assert r"another-.*" in ModelRegistry._registry
        assert ModelRegistry._registry[r"test-.*"] == TestModelBase
        assert ModelRegistry._registry[r"another-.*"] == AnotherTestModel

    def test_register_as_decorator(self):
        """Test using register as a decorator."""

        @ModelRegistry.register
        class DecoratedModel(LLMModel):

            @classmethod
            def supported_models(cls) -> List[str]:
                return [r"decorated-.*"]

            async def _generate_async_impl(self,
                                           request: LlmRequest,
                                           stream: bool = False,
                                           ctx: InvocationContext | None = None) -> AsyncGenerator[LlmResponse, None]:
                yield LlmResponse(content=None)

            def validate_request(self, request: LlmRequest) -> None:
                pass

        # Verify decorator worked
        assert r"decorated-.*" in ModelRegistry._registry
        assert ModelRegistry._registry[r"decorated-.*"] == DecoratedModel

    def test_resolve_exact_match(self):
        """Test resolving model name with exact pattern match."""
        ModelRegistry.register(TestModelBase)

        model_class = ModelRegistry.resolve("test-model-1")

        assert model_class == TestModelBase

    def test_resolve_case_insensitive(self):
        """Test that resolve is case insensitive."""
        ModelRegistry.register(TestModelBase)

        model_class = ModelRegistry.resolve("TEST-MODEL-1")

        assert model_class == TestModelBase

    def test_resolve_not_found(self):
        """Test resolving non-existent model raises ValueError."""
        ModelRegistry.register(TestModelBase)

        with pytest.raises(ValueError, match="No model implementation found"):
            ModelRegistry.resolve("nonexistent-model")

    def test_resolve_with_multiple_patterns(self):
        """Test resolving when multiple patterns are registered."""
        ModelRegistry.register(TestModelBase)
        ModelRegistry.register(AnotherTestModel)

        # Resolve test model
        test_class = ModelRegistry.resolve("test-model")
        assert test_class == TestModelBase

        # Resolve another model
        another_class = ModelRegistry.resolve("another-model")
        assert another_class == AnotherTestModel

    def test_create_model_basic(self):
        """Test creating a model instance."""
        ModelRegistry.register(TestModelBase)

        model = ModelRegistry.create_model("test-model-1", api_key="test_key")

        assert isinstance(model, TestModelBase)
        assert model.name == "test-model-1"
        assert model._api_key == "test_key"

    def test_create_model_with_kwargs(self):
        """Test creating model with additional configuration."""
        ModelRegistry.register(TestModelBase)

        model = ModelRegistry.create_model("test-model",
                                           api_key="test_key",
                                           base_url="https://test.com",
                                           custom_param="value")

        assert isinstance(model, TestModelBase)
        assert model._api_key == "test_key"
        assert model._base_url == "https://test.com"
        assert model.config.get("custom_param") == "value"

    def test_create_model_not_registered(self):
        """Test creating model that's not registered raises ValueError."""
        with pytest.raises(ValueError, match="No model implementation found"):
            ModelRegistry.create_model("unregistered-model")

    def test_list_supported_models(self):
        """Test listing all supported model patterns."""
        ModelRegistry.register(TestModelBase)
        ModelRegistry.register(AnotherTestModel)

        supported = ModelRegistry.list_supported_models()

        assert r"test-.*" in supported
        assert r"another-.*" in supported
        assert len(supported) == 2

    def test_list_models_empty(self):
        """Test listing models when registry is empty."""
        supported = ModelRegistry.list_supported_models()
        classes = ModelRegistry.list_registered_classes()

        assert len(supported) == 0
        assert len(classes) == 0

    def test_list_registered_classes(self):
        """Test listing all registered model classes."""
        ModelRegistry.register(TestModelBase)
        ModelRegistry.register(AnotherTestModel)

        classes = ModelRegistry.list_registered_classes()

        assert TestModelBase in classes
        assert AnotherTestModel in classes
        assert len(classes) == 2

    def test_register_model_decorator(self):
        """Test register_model decorator function."""

        @register_model(model_name="CustomModel", supported_models=[r"custom-.*", r"special-.*"])
        class CustomModel(LLMModel):

            async def _generate_async_impl(self,
                                           request: LlmRequest,
                                           stream: bool = False,
                                           ctx: InvocationContext | None = None) -> AsyncGenerator[LlmResponse, None]:
                yield LlmResponse(content=None)

            def validate_request(self, request: LlmRequest) -> None:
                pass

        # Verify both patterns are registered
        assert r"custom-.*" in ModelRegistry._registry
        assert r"special-.*" in ModelRegistry._registry

        # Verify display name is set (access as property since it's a property on the class)
        instance = CustomModel(model_name="custom-test", api_key="test_key")
        assert instance.display_name == "CustomModel"

        # Verify supported_models method works
        assert r"custom-.*" in CustomModel.supported_models()
        assert r"special-.*" in CustomModel.supported_models()

    def test_register_model_decorator_with_single_pattern(self):
        """Test register_model decorator with single pattern."""

        @register_model(model_name="SingleModel", supported_models=[r"single-.*"])
        class SingleModel(LLMModel):

            async def _generate_async_impl(self,
                                           request: LlmRequest,
                                           stream: bool = False,
                                           ctx: InvocationContext | None = None) -> AsyncGenerator[LlmResponse, None]:
                yield LlmResponse(content=None)

            def validate_request(self, request: LlmRequest) -> None:
                pass

        # Verify pattern is registered
        assert r"single-.*" in ModelRegistry._registry
        model_class = ModelRegistry.resolve("single-test")
        assert model_class == SingleModel

    def test_overwrite_existing_pattern(self):
        """Test that registering a new model with existing pattern overwrites it."""
        ModelRegistry.register(TestModelBase)

        # Register another model with same pattern
        @register_model(model_name="NewModel", supported_models=[r"test-.*"])
        class NewTestModel(LLMModel):

            async def _generate_async_impl(self,
                                           request: LlmRequest,
                                           stream: bool = False,
                                           ctx: InvocationContext | None = None) -> AsyncGenerator[LlmResponse, None]:
                yield LlmResponse(content=None)

            def validate_request(self, request: LlmRequest) -> None:
                pass

        # Clear cache to test overwrite
        ModelRegistry.resolve.cache_clear()

        # The new model should be resolved
        model_class = ModelRegistry.resolve("test-model")
        assert model_class == NewTestModel
