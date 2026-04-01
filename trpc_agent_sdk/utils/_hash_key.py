# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Hash key utilities for TRPC Agent framework."""


def user_key(app_name: str, user_id: str) -> str:
    """Generate a key for the user."""
    return f'{app_name}/{user_id}'
