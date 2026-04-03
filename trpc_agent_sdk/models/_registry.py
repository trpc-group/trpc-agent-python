# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Model registry implementation module.

This module provides the ModelRegistry class which manages registration and
resolution of model implementations. It supports:
- Model class registration
- Model name pattern matching
- Model instance creation
- Decorator-based registration
"""

import re
from functools import lru_cache
from typing import Callable
from typing import Dict
from typing import List
from typing import Type
from typing_extensions import override

from ._llm_model import LLMModel


class ModelRegistry:
    """Registry for model implementations."""

    _registry: Dict[str, Type[LLMModel]] = {}

    @classmethod
    def _register_class(cls, model_class: Type[LLMModel]) -> None:
        """Internal method to register a model class.

        Args:
            model_class: The model class to register
        """
        for pattern in model_class.supported_models():
            cls._registry[pattern] = model_class

    @classmethod
    def register(cls, model_class: Type[LLMModel]) -> Type[LLMModel]:
        """Register a model class (can be used as decorator or function).

        Args:
            model_class: The model class to register

        Returns:
            The same model class (for decorator usage)
        """
        cls._register_class(model_class)
        return model_class

    @classmethod
    @lru_cache(maxsize=128)
    def resolve(cls, model_name: str) -> Type[LLMModel]:
        """Resolve model name to model class.

        Args:
            model_name: The model name to resolve

        Returns:
            The model class

        Raises:
            ValueError: If model is not found
        """
        for pattern, model_class in cls._registry.items():
            if re.match(pattern, model_name.lower()):
                return model_class

        raise ValueError(f"No model implementation found for: {model_name}")

    @classmethod
    def create_model(cls, model_name: str, **kwargs) -> LLMModel:
        """Create a model instance.

        Args:
            model_name: The model name
            **kwargs: Additional configuration

        Returns:
            Model instance
        """
        model_class = cls.resolve(model_name)
        return model_class(model_name, **kwargs)

    @classmethod
    def list_supported_models(cls) -> List[str]:
        """List all supported model patterns."""
        return list(cls._registry.keys())

    @classmethod
    def list_registered_classes(cls) -> List[Type[LLMModel]]:
        """List all registered model classes."""
        return list(set(cls._registry.values()))


def register_model(model_name: str, supported_models: List[str]) -> Callable:
    """Decorator to register a model class.

    Args:
        model_name: The display name for this model implementation
        supported_models: Regex pattern to match model names

    Returns:
        Decorator function

    Usage:
        @register_model(model_name="OpenAIModel", supported_models=[r"gpt-.*"])
        class OpenAIModel(BaseModel):
            ...
    """

    def decorator(model_class: Type[LLMModel]) -> Type[LLMModel]:
        # Create a new class that inherits from the decorated class
        # This ensures the abstract method is properly implemented
        class _RegisteredModel(model_class):

            @override
            @classmethod
            def supported_models(cls) -> List[str]:
                return supported_models

        # Copy over class attributes
        _RegisteredModel.__name__ = model_class.__name__
        _RegisteredModel.__qualname__ = model_class.__qualname__
        _RegisteredModel.__module__ = model_class.__module__
        _RegisteredModel._model_display_name = model_name

        # Register the new model class
        ModelRegistry._register_class(_RegisteredModel)
        return _RegisteredModel

    return decorator
