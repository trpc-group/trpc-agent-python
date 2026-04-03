# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
Singleton decorator implementation.
This module provides a decorator to make a class follow the singleton pattern.
"""
from typing import Any
from typing import Dict
from typing import Type


def singleton(cls):
    """Decorator function that turns a class into a singleton.

    Args:
        cls: The class to be decorated as a singleton.

    Returns:
        A function that controls the instantiation of the class, ensuring only one instance exists.
    """
    instances = {}

    def get_instance(*args, **kw):
        """Inner function that manages the singleton instance.

        Args:
            *args: Positional arguments to pass to the class constructor.
            **kw: Keyword arguments to pass to the class constructor.

        Returns:
            The single instance of the decorated class.
        """
        if cls not in instances:
            instances[cls] = cls(*args, **kw)
        return instances[cls]

    return get_instance


class SingletonMeta(type):
    """Metaclass for implementing singleton pattern.

    This metaclass ensures that only one instance exists for each child class.
    Thread-safe implementation using double-checked locking pattern.
    """

    _instances: Dict[Type[Any], Any] = {}

    def __call__(cls, *args: Any, **kwargs: Any) -> Any:
        """Override __call__ to control instance creation.

        Args:
            *args: Positional arguments for initialization
            **kwargs: Keyword arguments for initialization

        Returns:
            The single instance of the class
        """
        if cls not in cls._instances:
            if cls not in cls._instances:
                instance = super().__call__(*args, **kwargs)
                cls._instances[cls] = instance
        return cls._instances[cls]


class SingletonBase(metaclass=SingletonMeta):
    """Base class for singleton implementation.

    Any class inheriting from this will automatically become a singleton.
    """

    def __init__(self) -> None:
        """Initialize the singleton instance.

        Note: This will only be called once per child class.
        """
        pass
