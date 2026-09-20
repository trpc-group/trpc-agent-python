# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tencent Cloud Agent Sandbox configuration for the Cube/E2B client."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from typing import Mapping
from typing import Optional

from ._types import CubeClientConfig

ENV_TENCENT_AGS_DOMAIN = "E2B_DOMAIN"


@dataclass
class TencentAGSClientConfig(CubeClientConfig):
    """Connect :class:`CubeSandboxClient` to Tencent Cloud Agent Sandbox.

    Tencent AGS exposes an E2B-compatible endpoint selected by ``domain``.
    Its API keys do not use the public E2B key format, so local key-format
    validation is disabled by default while remote authentication remains in
    force.

    An explicit ``api_url`` takes precedence over an explicit ``domain``.
    Environment values are consulted only when neither endpoint field is set,
    preserving the usual explicit-configuration-over-environment precedence.
    The API URL form supports private gateways and proxies without requiring a
    second provider-specific configuration type.
    """

    domain: Optional[str] = None
    """Tencent AGS domain. Falls back to ``E2B_DOMAIN``."""

    validate_api_key: bool = False
    """Whether the E2B SDK should validate the API key format locally."""

    request_timeout: Optional[float] = None
    """Optional E2B control-plane request timeout in seconds."""

    metadata: Optional[Mapping[str, str]] = None
    """Metadata attached when a new sandbox is created."""

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.domain is not None:
            self._validate_domain(self.domain)
        if not isinstance(self.validate_api_key, bool):
            raise TypeError("validate_api_key must be a bool")
        if self.request_timeout is not None:
            if isinstance(self.request_timeout, bool) or not isinstance(self.request_timeout, (int, float)):
                raise TypeError("request_timeout must be a number of seconds")
            if self.request_timeout <= 0:
                raise ValueError("request_timeout must be > 0 seconds")

    def resolve_domain(self) -> str:
        """Resolve the Tencent AGS domain from the field or environment."""
        value = self.domain or os.getenv(ENV_TENCENT_AGS_DOMAIN)
        if not value:
            raise ValueError("Tencent AGS requires `domain`, E2B_DOMAIN, or an explicit "
                             "`api_url` / E2B_API_URL.")
        return self._validate_domain(value)

    @staticmethod
    def _validate_domain(value: str) -> str:
        if not isinstance(value, str):
            raise TypeError("domain must be a string containing a bare hostname")
        if (not value or value != value.strip() or any(char.isspace() for char in value)
                or any(marker in value for marker in (":", "/", "\\", "?", "#"))):
            raise ValueError("domain must be a bare hostname such as "
                             "'ap-guangzhou.tencentags.com'; use api_url for a full URL")
        return value

    @staticmethod
    def _api_url_for_domain(domain: str) -> str:
        """Build the control-plane URL using the E2B SDK's domain rule."""
        return f"https://api.{domain}"

    def _e2b_connection_kwargs(self) -> dict[str, Any]:
        configured_domain = self.domain or os.getenv(ENV_TENCENT_AGS_DOMAIN)
        domain = self.resolve_domain() if configured_domain else None

        if self.api_url:
            endpoint = {"api_url": self.api_url}
            if domain is not None:
                endpoint["domain"] = domain
        elif domain is not None:
            # ConnectionConfig resolves api_url as: explicit value,
            # E2B_API_URL, then https://api.<domain>. Pass the derived URL
            # explicitly so a stale E2B_API_URL cannot override this domain.
            endpoint = {
                "api_url": self._api_url_for_domain(domain),
                "domain": domain,
            }
        else:
            try:
                endpoint = {"api_url": self.resolve_api_url()}
            except ValueError as exc:
                raise ValueError("Tencent AGS requires `domain`, E2B_DOMAIN, or an explicit "
                                 "`api_url` / E2B_API_URL.") from exc
        kwargs: dict[str, Any] = {
            **endpoint,
            "api_key": self.resolve_api_key(),
            "validate_api_key": self.validate_api_key,
        }
        if self.request_timeout is not None:
            kwargs["request_timeout"] = self.request_timeout
        return kwargs

    def _e2b_create_kwargs(self) -> dict[str, Any]:
        kwargs = super()._e2b_create_kwargs()
        if self.metadata is not None:
            kwargs["metadata"] = dict(self.metadata)
        return kwargs
