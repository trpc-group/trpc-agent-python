# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License, Version 2.0.
"""共享的敏感配置识别、占位符替换与持久化校验规则。"""

from __future__ import annotations


API_KEY_PLACEHOLDER = "${TRPC_AGENT_API_KEY}"
BASE_URL_PLACEHOLDER = "${TRPC_AGENT_BASE_URL}"

_SENSITIVE_CONFIG_KEYS = {
    "accesstoken",
    "apikey",
    "auth",
    "authorization",
    "authtoken",
    "baseurl",
    "bearertoken",
    "clientsecret",
    "credential",
    "credentials",
    "password",
    "passwd",
    "privatekey",
    "secret",
    "secretkey",
    "token",
    "xapikey",
}
_SENSITIVE_CONFIG_KEY_SUFFIXES = {
    "accesstoken",
    "apikey",
    "authtoken",
    "baseurl",
    "bearertoken",
    "clientsecret",
    "credential",
    "credentials",
    "endpointurl",
    "password",
    "passwd",
    "privatekey",
    "secretkey",
}
_URL_CONFIG_KEY_SUFFIXES = {"baseurl", "endpointurl"}
_APPROVED_SENSITIVE_VALUES = {
    "",
    API_KEY_PLACEHOLDER,
    BASE_URL_PLACEHOLDER,
    "fake-not-used-in-offline-mode",
}


class SensitiveConfigError(ValueError):
    """配置中存在不允许持久化的连接信息或凭据。"""


def _normalized_key(key: str) -> str:
    return key.replace("_", "").replace("-", "").casefold()


def _is_sensitive_key(key: str) -> bool:
    normalized = _normalized_key(key)
    return normalized in _SENSITIVE_CONFIG_KEYS or any(
        normalized.endswith(suffix)
        for suffix in _SENSITIVE_CONFIG_KEY_SUFFIXES
    )


def _placeholder_for_key(key: str) -> str:
    normalized = _normalized_key(key)
    if any(normalized.endswith(suffix) for suffix in _URL_CONFIG_KEY_SUFFIXES):
        return BASE_URL_PLACEHOLDER
    return API_KEY_PLACEHOLDER


def replace_persisted_sensitive_values(value: object) -> object:
    """递归替换任何可能进入运行产物的连接地址和凭据。"""
    if isinstance(value, str):
        if value.strip().casefold().startswith(("http://", "https://")):
            return BASE_URL_PLACEHOLDER
        return value
    if isinstance(value, list):
        return [replace_persisted_sensitive_values(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        key: (
            _placeholder_for_key(key)
            if _is_sensitive_key(key)
            else replace_persisted_sensitive_values(item)
        )
        for key, item in value.items()
    }


def validate_persisted_sensitive_values(value: object, *, path: str = "$") -> None:
    """拒绝不符合共享占位符策略的持久化配置。"""
    if isinstance(value, str):
        if value.strip().casefold().startswith(("http://", "https://")):
            raise SensitiveConfigError(
                "sensitive optimizer config value is not an approved "
                f"placeholder: {path}"
            )
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            validate_persisted_sensitive_values(item, path=f"{path}[{index}]")
        return
    if not isinstance(value, dict):
        return
    for key, item in value.items():
        item_path = f"{path}.{key}"
        if _is_sensitive_key(key):
            if not isinstance(item, str) or item not in _APPROVED_SENSITIVE_VALUES:
                raise SensitiveConfigError(
                    "sensitive optimizer config value is not an approved "
                    f"placeholder: {item_path}"
                )
        else:
            validate_persisted_sensitive_values(item, path=item_path)
