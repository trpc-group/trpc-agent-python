# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for Tencent Cloud Agent Sandbox configuration."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from e2b import ConnectionConfig

from trpc_agent_sdk.code_executors.cube import CubeSandboxClient
from trpc_agent_sdk.code_executors.cube import TencentAGSClientConfig


def _cfg(**overrides) -> TencentAGSClientConfig:
    values = {
        "template": "tmpl",
        "api_key": "ark-secret",
        "domain": "ap-guangzhou.tencentags.com",
        "idle_timeout": 123,
        "execute_timeout": 45.0,
    }
    values.update(overrides)
    return TencentAGSClientConfig(**values)


class TestTencentAGSClientConfig:

    def test_defaults_disable_local_api_key_format_validation(self):
        assert _cfg().validate_api_key is False

    def test_domain_falls_back_to_environment(self, monkeypatch):
        monkeypatch.setenv("E2B_DOMAIN", "ap-shanghai.tencentags.com")
        assert _cfg(domain=None).resolve_domain() == "ap-shanghai.tencentags.com"

    def test_missing_endpoint_raises(self, monkeypatch):
        monkeypatch.delenv("E2B_API_URL", raising=False)
        monkeypatch.delenv("E2B_DOMAIN", raising=False)
        with pytest.raises(ValueError, match="E2B_DOMAIN"):
            _cfg(domain=None)._e2b_connection_kwargs()

    @pytest.mark.parametrize("request_timeout", [0, -1.0])
    def test_rejects_non_positive_request_timeout(self, request_timeout):
        with pytest.raises(ValueError, match="request_timeout must be > 0"):
            _cfg(request_timeout=request_timeout)

    @pytest.mark.parametrize("request_timeout", [True, "30"])
    def test_rejects_non_numeric_request_timeout(self, request_timeout):
        with pytest.raises(TypeError, match="request_timeout must be a number"):
            _cfg(request_timeout=request_timeout)

    def test_rejects_non_bool_api_key_validation(self):
        with pytest.raises(TypeError, match="validate_api_key must be a bool"):
            _cfg(validate_api_key=0)

    @pytest.mark.parametrize("domain", [
        "https://ap-guangzhou.tencentags.com",
        "ap-guangzhou.tencentags.com/v1",
        "ap-guangzhou.tencentags.com:443",
        " ap-guangzhou.tencentags.com",
    ])
    def test_rejects_domain_that_is_not_a_bare_hostname(self, domain):
        with pytest.raises(ValueError, match="domain must be a bare hostname"):
            _cfg(domain=domain)

    def test_rejects_invalid_environment_domain_when_resolved(self, monkeypatch):
        monkeypatch.setenv("E2B_DOMAIN", "https://ap-guangzhou.tencentags.com")
        cfg = _cfg(domain=None, api_url=None)

        with pytest.raises(ValueError, match="domain must be a bare hostname"):
            cfg._e2b_connection_kwargs()


class TestTencentAGSClientConnectionOptions:

    @pytest.mark.asyncio
    async def test_create_uses_domain_and_creation_metadata(self, fake_e2b, fake_async_sandbox):
        fake_e2b.AsyncSandbox.create = AsyncMock(return_value=fake_async_sandbox)
        cfg = _cfg(
            request_timeout=30.0,
            metadata={"application": "agent-test"},
        )

        await CubeSandboxClient.open_new(cfg)

        fake_e2b.AsyncSandbox.create.assert_awaited_once_with(
            template="tmpl",
            timeout=123,
            api_url="https://api.ap-guangzhou.tencentags.com",
            domain="ap-guangzhou.tencentags.com",
            api_key="ark-secret",
            validate_api_key=False,
            request_timeout=30.0,
            metadata={"application": "agent-test"},
        )

    @pytest.mark.asyncio
    async def test_attach_omits_creation_only_options(self, fake_e2b, fake_async_sandbox):
        fake_e2b.AsyncSandbox.connect = AsyncMock(return_value=fake_async_sandbox)
        cfg = _cfg(
            sandbox_id="sbx-42",
            request_timeout=30.0,
            metadata={"application": "agent-test"},
        )

        await CubeSandboxClient.open_existing(cfg)

        fake_e2b.AsyncSandbox.connect.assert_awaited_once_with(
            "sbx-42",
            api_url="https://api.ap-guangzhou.tencentags.com",
            domain="ap-guangzhou.tencentags.com",
            api_key="ark-secret",
            validate_api_key=False,
            request_timeout=30.0,
        )

    @pytest.mark.asyncio
    async def test_explicit_api_url_takes_precedence_over_domain(self, fake_e2b, fake_async_sandbox):
        fake_e2b.AsyncSandbox.create = AsyncMock(return_value=fake_async_sandbox)

        await CubeSandboxClient.open_new(_cfg(api_url="https://private-gateway.example.com"))

        kwargs = fake_e2b.AsyncSandbox.create.await_args.kwargs
        assert kwargs["api_url"] == "https://private-gateway.example.com"
        assert kwargs["domain"] == "ap-guangzhou.tencentags.com"

    @pytest.mark.asyncio
    async def test_explicit_domain_takes_precedence_over_environment_api_url(self, fake_e2b, fake_async_sandbox,
                                                                             monkeypatch):
        fake_e2b.AsyncSandbox.create = AsyncMock(return_value=fake_async_sandbox)
        monkeypatch.setenv("E2B_API_URL", "https://stale.example.com")

        await CubeSandboxClient.open_new(_cfg(domain="ap-shanghai.tencentags.com"))

        kwargs = fake_e2b.AsyncSandbox.create.await_args.kwargs
        assert kwargs["domain"] == "ap-shanghai.tencentags.com"
        assert kwargs["api_url"] == "https://api.ap-shanghai.tencentags.com"

        sdk_config = ConnectionConfig(**_cfg(domain="ap-shanghai.tencentags.com")._e2b_connection_kwargs())
        assert sdk_config.domain == "ap-shanghai.tencentags.com"
        assert sdk_config.api_url == "https://api.ap-shanghai.tencentags.com"

    @pytest.mark.asyncio
    async def test_environment_domain_takes_precedence_over_environment_api_url(self, fake_e2b, fake_async_sandbox,
                                                                                monkeypatch):
        fake_e2b.AsyncSandbox.create = AsyncMock(return_value=fake_async_sandbox)
        monkeypatch.setenv("E2B_DOMAIN", "ap-shanghai.tencentags.com")
        monkeypatch.setenv("E2B_API_URL", "https://stale.example.com")

        await CubeSandboxClient.open_new(_cfg(domain=None))

        kwargs = fake_e2b.AsyncSandbox.create.await_args.kwargs
        assert kwargs["domain"] == "ap-shanghai.tencentags.com"
        assert kwargs["api_url"] == "https://api.ap-shanghai.tencentags.com"

    @pytest.mark.asyncio
    async def test_environment_api_url_is_used_when_endpoint_fields_are_empty(self, fake_e2b, fake_async_sandbox,
                                                                              monkeypatch):
        fake_e2b.AsyncSandbox.create = AsyncMock(return_value=fake_async_sandbox)
        monkeypatch.delenv("E2B_DOMAIN", raising=False)
        monkeypatch.setenv("E2B_API_URL", "https://env-gateway.example.com")

        await CubeSandboxClient.open_new(_cfg(domain=None))

        kwargs = fake_e2b.AsyncSandbox.create.await_args.kwargs
        assert kwargs["api_url"] == "https://env-gateway.example.com"
        assert "domain" not in kwargs

    @pytest.mark.asyncio
    async def test_metadata_is_copied_before_passing_to_sdk(self, fake_e2b, fake_async_sandbox):
        fake_e2b.AsyncSandbox.create = AsyncMock(return_value=fake_async_sandbox)
        metadata = {"application": "agent-test"}

        await CubeSandboxClient.open_new(_cfg(metadata=metadata))

        passed_metadata = fake_e2b.AsyncSandbox.create.await_args.kwargs["metadata"]
        assert passed_metadata == metadata
        assert passed_metadata is not metadata
