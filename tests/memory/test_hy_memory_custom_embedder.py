# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the Hy-Memory example's custom BGE-M3 adapter."""

from unittest.mock import AsyncMock

import pytest

from examples.memory_service_with_hy_memory.custom_embedder import (
    BgeM3RemoteEmbeddingService, )


def _service(dimensions: int = 3) -> BgeM3RemoteEmbeddingService:
    return BgeM3RemoteEmbeddingService(
        endpoint="http://embedding.example/batch",
        x_client_ssid="ssid",
        x_host_key_access="access",
        dimensions=dimensions,
    )


def test_parse_supported_response_shapes():
    assert BgeM3RemoteEmbeddingService._parse_vectors({
        "code": 0,
        "data": {
            "embedding": [[1.0, 2.0]]
        },
    }) == [[1.0, 2.0]]
    assert BgeM3RemoteEmbeddingService._parse_vectors({
        "code": 0,
        "data": [{
            "embedding": [1.0, 2.0]
        }],
    }) == [[1.0, 2.0]]


async def test_embedding_service_protocol_methods():
    service = _service()
    service._request = AsyncMock(return_value=[[1.0, 2.0, 3.0]])

    vector = await service.embed("hello")
    vector_with_meta, metadata = await service.embed_with_meta("hello")
    queued = await service.embed_queued("hello")

    assert vector == [1.0, 2.0, 3.0]
    assert vector_with_meta == vector
    assert queued == vector
    assert metadata == {
        "provider": "bge-m3-remote",
        "dimensions": 3,
    }
    assert service.get_embedding_dims() == 3


async def test_rejects_count_and_dimension_mismatches():
    service = _service()
    service._request = AsyncMock(return_value=[])
    with pytest.raises(RuntimeError, match="count mismatch"):
        await service.embed_batch(["hello"])

    service._request = AsyncMock(return_value=[[1.0, 2.0]])
    with pytest.raises(RuntimeError, match="dimension mismatch"):
        await service.embed_batch(["hello"])
