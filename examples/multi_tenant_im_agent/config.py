"""Tenant configuration loader and account-to-tenant registry."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from threading import RLock

from .domain import ChannelBinding, TenantConfig


class ConfigurationError(ValueError):
    pass


class TenantNotFoundError(LookupError):
    pass


class TenantRegistry:
    """Immutable-at-request-time registry supporting atomic config reloads."""

    def __init__(self, tenants: Iterable[TenantConfig]):
        self._lock = RLock()
        self._tenants: dict[str, TenantConfig] = {}
        self._routes: dict[tuple[str, str], str] = {}
        self.replace(tenants)

    def replace(self, tenants: Iterable[TenantConfig]) -> None:
        next_tenants: dict[str, TenantConfig] = {}
        next_routes: dict[tuple[str, str], str] = {}
        for tenant in tenants:
            self._validate_tenant(tenant)
            if tenant.tenant_id in next_tenants:
                raise ConfigurationError(f"duplicate tenant_id: {tenant.tenant_id}")
            next_tenants[tenant.tenant_id] = tenant
            for binding in tenant.bindings:
                route = (binding.channel.lower(), binding.account_id)
                if route in next_routes:
                    raise ConfigurationError(
                        f"channel account is bound more than once: {route}"
                    )
                next_routes[route] = tenant.tenant_id
        with self._lock:
            self._tenants = next_tenants
            self._routes = next_routes

    @staticmethod
    def _validate_tenant(tenant: TenantConfig) -> None:
        if not tenant.tenant_id or not tenant.agent_app_id:
            raise ConfigurationError("tenant_id and agent_app_id are required")
        if not tenant.bindings:
            raise ConfigurationError(
                f"tenant {tenant.tenant_id} must have at least one channel binding"
            )
        if tenant.model_timeout_seconds <= 0:
            raise ConfigurationError("model_timeout_seconds must be positive")
        if tenant.session_lease_seconds <= tenant.model_timeout_seconds:
            raise ConfigurationError(
                "session_lease_seconds must be greater than model_timeout_seconds"
            )
        if (
            min(
                tenant.max_input_chars,
                tenant.request_token_budget,
                tenant.monthly_token_budget,
            )
            <= 0
        ):
            raise ConfigurationError("tenant input and token budgets must be positive")
        for binding in tenant.bindings:
            if (
                not binding.channel
                or not binding.account_id
                or not binding.webhook_secret_env
            ):
                raise ConfigurationError(
                    "channel, account_id and webhook_secret_env are required"
                )
            if binding.channel.lower() == "telegram" and not binding.bot_token_env:
                raise ConfigurationError("Telegram binding requires bot_token_env")
            if binding.channel.lower() == "wecom" and not binding.outbound_webhook_env:
                raise ConfigurationError("WeCom binding requires outbound_webhook_env")

    def resolve(
        self, channel: str, account_id: str
    ) -> tuple[TenantConfig, ChannelBinding]:
        route = (channel.lower(), account_id)
        with self._lock:
            tenant_id = self._routes.get(route)
            tenant = self._tenants.get(tenant_id or "")
        if tenant is None:
            raise TenantNotFoundError("unknown or disabled channel account")
        binding = next(
            (
                item
                for item in tenant.bindings
                if (item.channel.lower(), item.account_id) == route
            ),
            None,
        )
        if binding is None or not binding.enabled:
            raise TenantNotFoundError("unknown or disabled channel account")
        return tenant, binding

    def get(self, tenant_id: str) -> TenantConfig:
        with self._lock:
            tenant = self._tenants.get(tenant_id)
        if tenant is None:
            raise TenantNotFoundError("tenant not found")
        return tenant

    def public_summary(self) -> list[dict[str, object]]:
        with self._lock:
            tenants = tuple(self._tenants.values())
        return [
            {
                "tenant_id": tenant.tenant_id,
                "display_name": tenant.display_name,
                "agent_app_id": tenant.agent_app_id,
                "session_backend": tenant.session_backend.value,
                "channels": sorted(
                    binding.channel for binding in tenant.bindings if binding.enabled
                ),
            }
            for tenant in tenants
        ]

    def all(self) -> tuple[TenantConfig, ...]:
        with self._lock:
            return tuple(self._tenants.values())


def load_tenant_registry(path: str | Path) -> TenantRegistry:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    raw_tenants = data.get("tenants") if isinstance(data, dict) else data
    if not isinstance(raw_tenants, list):
        raise ConfigurationError("configuration must contain a tenants list")
    return TenantRegistry(TenantConfig.from_dict(item) for item in raw_tenants)


def require_secret(env_name: str) -> str:
    if not env_name:
        return ""
    value = os.environ.get(env_name, "")
    if not value:
        raise ConfigurationError(
            f"required secret environment variable is not set: {env_name}"
        )
    return value
