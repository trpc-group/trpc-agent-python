# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
""" Tools for the agent. """

import datetime


def get_current_date():
    """Get the current date and time in the format of YYYY-MM-DD."""
    return datetime.datetime.now().strftime("%Y-%m-%d")
