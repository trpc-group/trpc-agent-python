# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Registry factory for TRPC Agent framework."""

from typing import Dict
from typing import Generic
from typing import Optional
from typing import Type
from typing import TypeVar

from ._singleton import singleton

# Third-party imports (alphabetical)
# (None for this implementation)

# Local imports (alphabetical)
# (None for this implementation)

_T = TypeVar('_T')


class BaseRegistryFactory(Generic[_T]):
    """Registry for managing template classes with type-specific registries.

    Provides class registration and retrieval functionality for different types.
    """

    def __init__(self) -> None:
        super().__init__()
        self._cls_map: Dict[str, Type[_T]] = {}
        self._instance_map: Dict[str, _T] = {}

    def register(self, name: str, cls: Type[_T]) -> Type[_T]:
        """Register a class for a specific type.

        Args:
            target_type: The type this registration is for
            name: Unique identifier for the registration
            cls: Class to register

        Raises:
            TypeError: If name conflict exists
        """
        if name in self._cls_map:
            raise TypeError(f"Name '{name}' already registered for type {name}")
        self._cls_map[name] = cls
        return cls

    def get_instance(self, name: str) -> Optional[_T]:
        """Get a registered instance by type and name."""
        return self._instance_map.get(name, None)

    def get_cls(self, name: str) -> Optional[Type[_T]]:
        """Get a registered class by type and name.

        Args:
            target_type: The type to look up
            name: Name of the registered class

        Returns:
            The registered class or None if not found
        """
        return self._cls_map.get(name, None)

    def list_cls(self) -> Dict[str, Type[_T]]:
        """Get all registered classes for a specific type.

        Args:
            target_type: The type to list registrations for

        Returns:
            Dictionary of name to class mappings
        """
        return self._cls_map.copy()

    def list_instance(self) -> Dict[str, _T]:
        """Get all registered instances for a specific type.

        Args:
            target_type: The type to list registrations for

        Returns:
            Dictionary of name to class mappings
        """
        return self._instance_map.copy()

    def create(self, cls_type: str, *args, **kwargs) -> _T:
        """Create an instance of the class registered with the given key.

        Args:
            key: The key/type of the class to instantiate
            *args: Positional arguments to pass to the constructor
            **kwargs: Keyword arguments to pass to the constructor

        Returns:
            New instance of the registered class

        Raises:
            KeyError: If no class is registered for the given key
        """
        if cls_type not in self._cls_map:
            raise KeyError(f"No class registered for name: {cls_type}")
        return self._cls_map[cls_type](*args, **kwargs)

    def create_and_save(self, cls_type: str, obj_name: str, *args, **kwargs) -> _T:
        """Create an instance of the class registered with the given key.

        Args:
            key: The key/type of the class to instantiate
            *args: Positional arguments to pass to the constructor
            **kwargs: Keyword arguments to pass to the constructor

        Returns:
            New instance of the registered class

        Raises:
            KeyError: If no class is registered for the given key
        """
        if obj_name in self._instance_map:
            raise KeyError(f"Instance already exists for name: {cls_type}")
        instance = self.create(cls_type, *args, **kwargs)
        self._instance_map[obj_name] = instance
        return instance


# Example usage
if __name__ == "__main__":

    class Cat:
        """Example class representing a cat."""

        def render(self) -> str:
            """Render the cat's output.

            Returns:
                String representation of the cat
            """
            return "This is a cat"

    class Dog:
        """Example class representing a dog."""

        def render(self) -> str:
            """Render the dog's output.

            Returns:
                String representation of the dog
            """
            return "This is a dog"

    @singleton
    class CatRegistryFactory(BaseRegistryFactory[Cat]):
        pass

    @singleton
    class DogRegistryFactory(BaseRegistryFactory[Dog]):
        pass

    # Register classes
    CatRegistryFactory().register('cat', Cat)
    DogRegistryFactory().register('dog', Dog)

    # Get registered classes
    # Create instances
    cat = CatRegistryFactory().create('cat')
    dog = DogRegistryFactory().create('dog')

    print(cat.render())  # Output: This is a cat
    print(dog.render())  # Output: This is a dog

    # List all registered classes for each type
    print(CatRegistryFactory().list_cls())  # Output: {'cat': <class '__main__.Cat'>}
    print(DogRegistryFactory().list_cls())  # Output: {'dog': <class '__main__.Dog'>}
