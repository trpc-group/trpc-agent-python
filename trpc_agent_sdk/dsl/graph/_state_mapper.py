# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""State mapping utilities for subgraph nodes.

This module provides utilities for transforming state between parent graphs
and child subgraphs, enabling flexible data flow control.

Example:
    >>> from trpc_agent_sdk.dsl.graph import STATE_KEY_USER_INPUT
    >>> from trpc_agent_sdk.dsl.graph import STATE_KEY_METADATA
    >>>
    >>> # Pick specific fields from parent state
    >>> graph.add_agent_node(
    ...     "researcher",
    ...     agent=research_agent,
    ...     input_mapper=StateMapper.pick("query", "context"),
    ...     output_mapper=StateMapper.merge_response("research_result"),
    ... )
    >>>
    >>> # Rename fields during mapping
    >>> graph.add_agent_node(
    ...     "analyzer",
    ...     agent=analyzer_agent,
    ...     input_mapper=StateMapper.rename({STATE_KEY_USER_INPUT: "text_to_analyze"}),
    ... )
"""

from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Callable

# Type aliases for clarity
StateDict = dict[str, Any]


@dataclass
class SubgraphResult:
    """Normalized result returned by an agent/subgraph node execution.

    Attributes:
        last_response: Final text response observed from the child agent.
        final_state: Reconstructed final child state after applying emitted deltas.
        raw_state_delta: Raw merged state deltas emitted by the child run.
        structured_output: Optional structured output extracted from child state.
    """

    last_response: str = ""
    final_state: StateDict = field(default_factory=dict)
    raw_state_delta: StateDict = field(default_factory=dict)
    structured_output: Any = None


class StateMapper:
    """Utilities for mapping state between parent and child graphs.

    Provides static methods for common state transformation patterns,
    making it easy to control what data flows between graphs.

    Example:
        >>> # Pick specific fields
        >>> mapper = StateMapper.pick("query", "context")
        >>>
        >>> # Rename fields
        >>> mapper = StateMapper.rename({"parent_field": "child_field"})
        >>>
        >>> # Merge response into parent state
        >>> mapper = StateMapper.merge_response("result_field")
    """

    @staticmethod
    def pick(*fields: str) -> Callable[[StateDict], StateDict]:
        """Create input mapper that picks specific fields from parent state.

        This is useful when you want to pass only a subset of the parent's
        state to the child graph, keeping the child's interface clean.

        Args:
            *fields: Field names to extract from parent state

        Returns:
            Mapper function that extracts specified fields

        Example:
            >>> # Only pass query and context to child
            >>> input_mapper = StateMapper.pick("query", "context")
            >>> graph.add_agent_node(
            ...     "researcher",
            ...     agent=research_agent,
            ...     input_mapper=input_mapper
            ... )
        """

        def mapper(state: StateDict) -> StateDict:
            return {k: state[k] for k in fields if k in state}

        return mapper

    @staticmethod
    def rename(mapping: dict[str, str]) -> Callable[[StateDict], StateDict]:
        """Create input mapper that renames fields.

        This is useful when parent and child use different field names
        for the same conceptual data.

        Args:
            mapping: Dictionary mapping parent field names to child field names
                    Format: {parent_key: child_key}

        Returns:
            Mapper function that renames fields

        Example:
            >>> from trpc_agent_sdk.dsl.graph import STATE_KEY_USER_INPUT
            >>> # Rename parent's "query" to child's STATE_KEY_USER_INPUT
            >>> input_mapper = StateMapper.rename({
            ...     "query": STATE_KEY_USER_INPUT,
            ...     "docs": "context"
            ... })
            >>> graph.add_agent_node("processor", agent=processor_agent, input_mapper=input_mapper)
        """

        def mapper(state: StateDict) -> StateDict:
            result = {}
            for parent_key, child_key in mapping.items():
                if parent_key in state:
                    result[child_key] = state[parent_key]
            return result

        return mapper

    @staticmethod
    def merge_response(target_field: str) -> Callable[[StateDict, SubgraphResult], StateDict]:
        """Create output mapper that stores child's response in a specific field.

        This is useful for capturing the child's result and storing it under
        a specific key in the parent's state.

        Args:
            target_field: Field name to store child's response in parent state

        Returns:
            Mapper function that extracts response and returns state update

        Example:
            >>> # Store child's response in "search_results" field
            >>> output_mapper = StateMapper.merge_response("search_results")
            >>> graph.add_agent_node(
            ...     "searcher",
            ...     agent=search_agent,
            ...     output_mapper=output_mapper
            ... )
        """

        def mapper(parent_state: StateDict, child_result: SubgraphResult) -> StateDict:
            del parent_state
            return {target_field: child_result.last_response}

        return mapper

    @staticmethod
    def identity() -> Callable[[StateDict], StateDict]:
        """Create identity mapper that passes state through unchanged.

        Returns:
            Mapper function that returns state as-is

        Example:
            >>> # Pass entire parent state to child
            >>> input_mapper = StateMapper.identity()
        """

        def mapper(state: StateDict) -> StateDict:
            return dict(state)

        return mapper

    @staticmethod
    def combine(*mappers: Callable[[StateDict], StateDict]) -> Callable[[StateDict], StateDict]:
        """Combine multiple input mappers into a single mapper.

        The result is a union of all mapper outputs. If multiple mappers
        produce the same key, the last mapper's value wins.

        Args:
            *mappers: Mapper functions to combine

        Returns:
            Combined mapper function

        Example:
            >>> from trpc_agent_sdk.dsl.graph import STATE_KEY_USER_INPUT
            >>> from trpc_agent_sdk.dsl.graph import STATE_KEY_METADATA
            >>> # Combine picking and renaming
            >>> input_mapper = StateMapper.combine(
            ...     StateMapper.pick("context", STATE_KEY_METADATA),
            ...     StateMapper.rename({"query": STATE_KEY_USER_INPUT})
            ... )
        """

        def mapper(state: StateDict) -> StateDict:
            result = {}
            for m in mappers:
                result.update(m(state))
            return result

        return mapper

    @staticmethod
    def filter_keys(predicate: Callable[[str], bool]) -> Callable[[StateDict], StateDict]:
        """Create input mapper that filters keys based on a predicate.

        Args:
            predicate: Function that takes a key and returns True to include it

        Returns:
            Mapper function that filters state keys

        Example:
            >>> # Only pass fields that start with "user_"
            >>> input_mapper = StateMapper.filter_keys(lambda k: k.startswith("user_"))
        """

        def mapper(state: StateDict) -> StateDict:
            return {k: v for k, v in state.items() if predicate(k)}

        return mapper

    @staticmethod
    def exclude(*fields: str) -> Callable[[StateDict], StateDict]:
        """Create input mapper that excludes specific fields.

        This is the opposite of `pick()` - it passes all fields except
        the specified ones.

        Args:
            *fields: Field names to exclude from parent state

        Returns:
            Mapper function that excludes specified fields

        Example:
            >>> # Pass everything except sensitive data
            >>> input_mapper = StateMapper.exclude("api_key", "password")
        """
        excluded = set(fields)

        def mapper(state: StateDict) -> StateDict:
            return {k: v for k, v in state.items() if k not in excluded}

        return mapper
