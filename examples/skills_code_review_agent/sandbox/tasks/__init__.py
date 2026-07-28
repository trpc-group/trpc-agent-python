"""Concrete sandbox task builders."""

from .custom_rule import build_code_review_task
from .diff_parser import build_diff_parser_task
from .run_test import build_test_task
from .static_check import build_static_check_task

__all__ = [
    "build_code_review_task",
    "build_diff_parser_task",
    "build_static_check_task",
    "build_test_task",
]
