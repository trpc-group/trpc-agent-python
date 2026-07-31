# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Scanner-safe secret sample builders for business tests."""

from __future__ import annotations


def openai_like_token() -> str:
    return _join("sk", "-", "alpha", "beta", "token", "value")


def openai_live_like_token() -> str:
    return _join("sk", "-", "live", "-", "alpha", "beta", "token", "value")


def github_pat_like_token() -> str:
    return _join("github", "_", "pat", "_", "alpha", "beta", "token", "value")


def github_classic_like_token() -> str:
    return _join("ghp", "_", "alpha", "beta", "token", "value")


def github_oauth_like_token() -> str:
    return _join("gho", "_", "alpha", "beta", "token", "value")


def slack_like_token() -> str:
    return _join("xoxb", "-", "alpha", "-", "beta", "-", "token", "-", "value")


def aws_access_key_like_token() -> str:
    return _join("AKIA", "QWER", "TYUI", "OPAS", "DFGH")


def aws_session_key_like_token() -> str:
    return _join("ASIA", "QWER", "TYUI", "OPAS", "DFGH")


def bearer_like_token() -> str:
    return _join("bearer", "-", "alpha", "-", "beta", "-", "token", "-", "value")


def jwt_like_token() -> str:
    return ".".join([
        _join("eyJ", "hbGci", "OiJI", "UzI1NiJ9"),
        _join("eyJ", "zdWIi", "OiIxMjMifQ"),
        "signature",
    ])


def db_password_like_value() -> str:
    return _join("db", "-", "password", "-", "value")


def client_secret_like_value() -> str:
    return _join("example", "-", "client", "-", "secret", "-", "value")


def refresh_token_like_value() -> str:
    return _join("example", "-", "refresh", "-", "token", "-", "value")


def private_key_body_value() -> str:
    return _join("example", "-", "private", "-", "key", "-", "body")


def generic_api_key_value() -> str:
    return _join("example", "-", "sensitive", "-", "api", "-", "key", "-", "value")


def generic_password_value() -> str:
    return _join("example", "-", "sensitive", "-", "password", "-", "value")


def generic_bearer_value() -> str:
    return _join("example", "-", "sensitive", "-", "bearer", "-", "token")


def generic_secret_value() -> str:
    return _join("example", "-", "sensitive", "-", "secret", "-", "value")


def forbidden_provider_literals() -> list[str]:
    return [
        _join("sk", "-", "live", "-", "1234567890abcdef"),
        _join("sk", "-", "abcdefghijklmnop"),
        _join("ghp", "_", "abcdefghijklmnop"),
        _join("gho", "_", "abcdefghijklmnop"),
        _join("github", "_", "pat", "_", "abcdefghijklmnop"),
        _join("xoxb", "-", "1234567890", "-", "abcdefghijklmnop"),
        _join("AKIA", "ABCDEFGHIJKLMNOP"),
        _join("ASIA", "ABCDEFGHIJKLMNOP"),
    ]


def _join(*parts: str) -> str:
    return "".join(parts)
