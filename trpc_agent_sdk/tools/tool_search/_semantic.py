# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Optional semantic index for deferred tools."""

from __future__ import annotations

import asyncio
import inspect
import math
import threading
from collections.abc import Mapping
from typing import Any
from typing import Callable
from typing import Optional
from typing import Protocol
from typing import Sequence

from ._types import ToolSearchDocument
from ._types import ToolSearchEmbeddingUsage


class ToolSearchVectorStore(Protocol):
    """Storage contract used by :class:`SemanticToolIndex`.

    Methods may be synchronous or asynchronous.
    """

    def upsert(self, name: str, vector: Sequence[float]) -> Any:
        """Insert or replace one named vector."""

    def delete(self, names: Sequence[str]) -> Any:
        """Delete vectors by name."""

    def search(
        self,
        query_vector: Sequence[float],
        candidate_names: Sequence[str],
        limit: int,
    ) -> Any:
        """Return candidate names ordered by descending similarity."""


class InMemoryToolSearchVectorStore:
    """Default process-local vector store."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._vectors: dict[str, list[float]] = {}

    def upsert(self, name: str, vector: Sequence[float]) -> None:
        with self._lock:
            self._vectors[name] = [float(value) for value in vector]

    def delete(self, names: Sequence[str]) -> None:
        with self._lock:
            for name in names:
                self._vectors.pop(name, None)

    def search(
        self,
        query_vector: Sequence[float],
        candidate_names: Sequence[str],
        limit: int,
    ) -> list[str]:
        allowed = set(candidate_names)
        with self._lock:
            scored = [(
                _cosine_similarity(query_vector, vector),
                name,
            ) for name, vector in self._vectors.items() if name in allowed]
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [name for _, name in scored[:limit]]


class SemanticToolIndex:
    """Rank tool declarations with an injected embedding implementation.

    The embedder may implement LangChain-style ``aembed_documents`` /
    ``aembed_query`` methods or their synchronous ``embed_*`` counterparts.
    No embedding dependency is required by the core package.
    """

    def __init__(
        self,
        embedder: Any,
        vector_store: Optional[ToolSearchVectorStore] = None,
    ) -> None:
        if embedder is None:
            raise ValueError("embedder must not be None")
        self._embedder = embedder
        self._vector_store = vector_store or InMemoryToolSearchVectorStore()
        self._lock = asyncio.Lock()
        self._fingerprints: dict[str, str] = {}

    async def search(
        self,
        query: str,
        candidates: Sequence[ToolSearchDocument],
        limit: int,
        verifier: Optional[Callable[[str, str], bool]] = None,
        usage: Optional[ToolSearchEmbeddingUsage] = None,
    ) -> list[str]:
        """Return candidate names ordered by descending cosine similarity."""
        if not query.strip() or not candidates or limit <= 0:
            return []

        async with self._lock:
            missing = [item for item in candidates if self._fingerprints.get(item.name) != item.fingerprint]
            if missing:
                vectors, document_usage = await self._embed_documents([item.text for item in missing])
                if len(vectors) != len(missing):
                    raise ValueError("embedding result count does not match tool document count", )
                if usage is not None:
                    usage.add(document_usage)
                for item, vector in zip(missing, vectors):
                    if verifier is not None and not verifier(
                            item.name,
                            item.fingerprint,
                    ):
                        continue
                    await _maybe_await(self._vector_store.delete([item.name]))
                    await _maybe_await(self._vector_store.upsert(
                        item.name,
                        [float(value) for value in vector],
                    ))
                    self._fingerprints[item.name] = item.fingerprint

            query_vector, query_usage = await self._embed_query(query)
            if usage is not None:
                usage.add(query_usage)
            candidate_names = [
                item.name for item in candidates if self._fingerprints.get(item.name) == item.fingerprint
            ]
            result = await _maybe_await(
                self._vector_store.search(
                    [float(value) for value in query_vector],
                    candidate_names,
                    limit,
                ))
        allowed = set(candidate_names)
        return [str(name) for name in result if str(name) in allowed][:limit]

    async def forget(self, names: Sequence[str]) -> None:
        """Remove cached vectors for tools that no longer exist."""
        if not names:
            return
        async with self._lock:
            for name in names:
                self._fingerprints.pop(name, None)
            await _maybe_await(self._vector_store.delete(names))

    async def _embed_documents(
        self,
        texts: list[str],
    ) -> tuple[list[list[float]], ToolSearchEmbeddingUsage]:
        method = getattr(self._embedder, "aembed_documents", None)
        if method is not None:
            result = method(texts)
            result = await result if inspect.isawaitable(result) else result
        else:
            method = getattr(self._embedder, "embed_documents", None)
            if method is None:
                raise TypeError("embedder must implement aembed_documents or embed_documents", )
            if inspect.iscoroutinefunction(method):
                result = await method(texts)
            else:
                result = await asyncio.to_thread(method, texts)
        values, usage = _split_embedding_result(result)
        return values, usage

    async def _embed_query(
        self,
        text: str,
    ) -> tuple[list[float], ToolSearchEmbeddingUsage]:
        method = getattr(self._embedder, "aembed_query", None)
        if method is not None:
            result = method(text)
            result = await result if inspect.isawaitable(result) else result
        else:
            method = getattr(self._embedder, "embed_query", None)
            if method is None:
                raise TypeError("embedder must implement aembed_query or embed_query", )
            if inspect.iscoroutinefunction(method):
                result = await method(text)
            else:
                result = await asyncio.to_thread(method, text)
        values, usage = _split_embedding_result(result)
        return values, usage


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _split_embedding_result(result: Any, ) -> tuple[Any, ToolSearchEmbeddingUsage]:
    if (isinstance(result, tuple) and len(result) == 2 and _looks_like_usage(result[1])):
        return result[0], _coerce_usage(result[1])
    return result, ToolSearchEmbeddingUsage()


def _looks_like_usage(value: Any) -> bool:
    if isinstance(value, Mapping):
        return True
    return any(
        hasattr(value, name) for name in (
            "prompt_tokens",
            "total_tokens",
            "prompt_token_count",
            "total_token_count",
        ))


def _coerce_usage(value: Any) -> ToolSearchEmbeddingUsage:

    def read(*names: str) -> int:
        for name in names:
            raw = value.get(name) if isinstance(value, Mapping) else getattr(
                value,
                name,
                None,
            )
            if raw is not None:
                try:
                    return int(raw)
                except (TypeError, ValueError):
                    return 0
        return 0

    return ToolSearchEmbeddingUsage(
        prompt_tokens=read("prompt_tokens", "prompt_token_count"),
        total_tokens=read("total_tokens", "total_token_count"),
    )


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return -1.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return -1.0
    return dot / (left_norm * right_norm)
