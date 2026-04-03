# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Hash key utilities for TRPC Agent framework."""


def user_key(app_name: str, user_id: str) -> str:
    """Generate a key for the user."""
    return f'{app_name}/{user_id}'
