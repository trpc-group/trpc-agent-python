# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Utility functions for memory service."""
import re
from datetime import datetime


def format_timestamp(timestamp: float) -> str:
    """Format the timestamp of the memory entry."""
    return datetime.fromtimestamp(timestamp).isoformat()


def extract_words_lower(text: str) -> set[str]:
    """Extract words from a string and convert them to lowercase.

    Extracts both English words and Chinese characters.
    For English: extracts words (sequences of letters)
    For Chinese: extracts individual characters (Unicode range \u4e00-\u9fff)
    """
    words = set()
    # Extract English words
    words.update([word.lower() for word in re.findall(r'[A-Za-z]+', text)])
    # Extract Chinese characters
    words.update(re.findall(r'[\u4e00-\u9fff]', text))
    return words
