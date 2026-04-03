# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
""" Tools for the agent. """

import datetime


def get_current_date():
    """Get the current date and time in the format of YYYY-MM-DD."""
    return datetime.datetime.now().strftime("%Y-%m-%d")
