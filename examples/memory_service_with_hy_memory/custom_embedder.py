# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Adapter for the internal BGE-M3 batch embedding HTTP API."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import aiohttp


class BgeM3RemoteEmbeddingService:
    """Implement Hy-Memory's public EmbeddingService protocol."""

    def __init__(
        self,
        *,
        endpoint: str,
        x_client_ssid: str,
        x_host_key_access: str,
        dimensions: int = 1024,
        max_retries: int = 2,
        retry_delay: float = 2.0,
        timeout: float = 60.0,
    ) -> None:
        if not endpoint:
            raise ValueError("BGE_M3_EMBEDDING_ENDPOINT must be set")
        if not x_client_ssid or not x_host_key_access:
            raise ValueError("X_CLIENT_SSID and X_HOST_KEY_ACCESS must be set", )
        self._endpoint = endpoint
        self._x_client_ssid = x_client_ssid
        self._x_host_key_access = x_host_key_access
        self._dimensions = dimensions
        self._max_retries = max(1, max_retries)
        self._retry_delay = retry_delay
        self._timeout = timeout

    @classmethod
    def from_env(cls) -> "BgeM3RemoteEmbeddingService":
        """Create the service from the example's environment variables."""
        # BGM_M3_* is retained as a fallback for the existing client spelling.
        endpoint = (os.getenv("BGE_M3_EMBEDDING_ENDPOINT") or os.getenv("BGM_M3_EMBEDDING_ENDPOINT", ""))
        return cls(
            endpoint=endpoint,
            x_client_ssid=os.getenv("X_CLIENT_SSID", ""),
            x_host_key_access=os.getenv("X_HOST_KEY_ACCESS", ""),
            dimensions=int(os.getenv("MEMORY_EMBEDDING_DIMS", "1024")),
            max_retries=int(os.getenv("BGE_M3_MAX_RETRIES", "2")),
            retry_delay=float(os.getenv("BGE_M3_RETRY_DELAY", "2")),
            timeout=float(os.getenv("BGE_M3_TIMEOUT", "60")),
        )

    def get_embedding_dims(self) -> int:
        """Return the vector dimension expected by Hy-Memory's store."""
        return self._dimensions

    async def embed(
        self,
        text: str,
        use_cache: bool = True,
    ) -> list[float]:
        """Embed one text."""
        del use_cache
        return (await self.embed_batch([text]))[0]

    async def embed_with_meta(
        self,
        text: str,
        use_cache: bool = True,
    ) -> tuple[list[float], dict[str, Any]]:
        """Embed one text and return provider metadata."""
        vector = await self.embed(text, use_cache=use_cache)
        return vector, {
            "provider": "bge-m3-remote",
            "dimensions": self._dimensions,
        }

    async def embed_batch(
        self,
        texts: list[str],
        use_cache: bool = True,
    ) -> list[list[float]]:
        """Embed a batch while preserving input/output cardinality."""
        del use_cache
        if not texts:
            return []
        normalized = []
        for text in texts:
            value = str(text)
            if not value.strip():
                raise ValueError("BGE-M3 embedding input must not be empty", )
            normalized.append(value)

        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                vectors = await self._request(normalized)
                self._validate_vectors(vectors, expected_count=len(normalized))
                return vectors
            except Exception as exc:  # pylint: disable=broad-except
                last_error = exc
                if attempt >= self._max_retries:
                    break
                await asyncio.sleep(self._retry_delay * attempt)
        raise RuntimeError(f"BGE-M3 embedding failed after {self._max_retries} attempts: "
                           f"{last_error}", ) from last_error

    async def embed_queued(
        self,
        text: str,
        use_cache: bool = True,
    ) -> list[float]:
        """Embed one queued request; the remote service handles batching."""
        return await self.embed(text, use_cache=use_cache)

    async def embed_queued_with_meta(
        self,
        text: str,
        use_cache: bool = True,
    ) -> tuple[list[float], dict[str, Any]]:
        """Embed one queued request and return provider metadata."""
        return await self.embed_with_meta(text, use_cache=use_cache)

    async def _request(self, texts: list[str]) -> list[list[float]]:
        headers = {
            "Content-Type": "application/json",
            "Cookie": (f"x-client-ssid={self._x_client_ssid}; "
                       f"x_host_key_access={self._x_host_key_access}"),
        }
        timeout = aiohttp.ClientTimeout(
            total=self._timeout,
            connect=min(10, self._timeout),
        )
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self._endpoint, headers=headers, json={"inputs": texts}) as response:
                response_text = await response.text()
                try:
                    result = json.loads(response_text)
                except json.JSONDecodeError as exc:
                    raise RuntimeError("BGE-M3 returned a non-JSON response: "
                                       f"status={response.status}", ) from exc

                if response.status != 200 or (isinstance(result, dict) and result.get("code", 0) != 0):
                    message = (result.get("message", "Unknown error") if isinstance(result, dict) else str(result))
                    raise RuntimeError(f"BGE-M3 request failed: status={response.status}, "
                                       f"message={message}", )
                return self._parse_vectors(result)

    @staticmethod
    def _parse_vectors(result: Any) -> list[list[float]]:
        if isinstance(result, dict):
            data = result.get("data")
            if isinstance(data, dict) and isinstance(data.get("embedding"), list):
                return data["embedding"]
            if isinstance(data, list):
                return [
                    item["embedding"] for item in data
                    if isinstance(item, dict) and isinstance(item.get("embedding"), list)
                ]
        if isinstance(result, list):
            return result
        raise RuntimeError("Unable to parse embeddings from BGE-M3 response")

    def _validate_vectors(
        self,
        vectors: list[list[float]],
        *,
        expected_count: int,
    ) -> None:
        if len(vectors) != expected_count:
            raise ValueError("BGE-M3 embedding count mismatch: "
                             f"expected={expected_count}, actual={len(vectors)}", )
        for index, vector in enumerate(vectors):
            if len(vector) != self._dimensions:
                raise ValueError(
                    "BGE-M3 embedding dimension mismatch: "
                    f"index={index}, expected={self._dimensions}, "
                    f"actual={len(vector)}", )
