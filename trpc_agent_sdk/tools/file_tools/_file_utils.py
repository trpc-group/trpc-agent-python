# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""File operation utility functions for common tools.

This module provides shared file operation helper functions for all common tools
in the TRPC Agent framework.
"""

from charset_normalizer import from_bytes


def _detect_encoding(file_path: str) -> str:
    """Detect file encoding using charset-normalizer (MIT license).

    Args:
        file_path: Path to file

    Returns:
        Detected encoding string
    """
    try:
        with open(file_path, "rb") as f:
            raw_data = f.read(10000)
            if not raw_data:
                return "utf-8"

            result = from_bytes(raw_data).best()
            if result and result.encoding:
                # charset-normalizer uses coherence (0.0-1.0) instead of confidence
                # Use encoding if coherence is reasonable (> 0.7) or if it's the only match
                encoding = result.encoding
                # Normalize encoding name: replace underscore with hyphen (utf_8 -> utf-8)
                # Python's codecs accepts both formats, but we normalize for consistency
                encoding = encoding.replace("_", "-")

                if result.coherence > 0.7:
                    return encoding
                elif encoding:
                    # If coherence is low but encoding is detected, still try it
                    return encoding

            return "utf-8"
    except Exception:  # pylint: disable=broad-except
        return _detect_encoding_fallback(file_path)


def _detect_encoding_fallback(file_path: str) -> str:
    """Fallback encoding detection using common encodings.

    Args:
        file_path: Path to file

    Returns:
        Detected encoding string
    """
    common_encodings = ["utf-8", "gbk", "gb2312", "latin-1"]
    try:
        with open(file_path, "rb") as f:
            raw_data = f.read(10000)
            for encoding in common_encodings:
                try:
                    raw_data.decode(encoding)
                    return encoding
                except UnicodeDecodeError:
                    continue
    except Exception:  # pylint: disable=broad-except
        pass
    return "utf-8"


def safe_read_file(path: str, encoding: str = "utf-8") -> tuple[str, str]:
    """Safely read file with automatic encoding detection.

    Args:
        path: File path
        encoding: Default encoding, used if detection fails

    Returns:
        (file content, actual encoding used)
    """
    try:
        with open(path, "r", encoding=encoding) as f:
            content = f.read()
            return content, encoding
    except UnicodeDecodeError:
        detected_encoding = _detect_encoding(path)
        try:
            with open(path, "r", encoding=detected_encoding) as f:
                content = f.read()
                return content, detected_encoding
        except Exception:  # pylint: disable=broad-except
            with open(path, "r", encoding=detected_encoding, errors="ignore") as f:
                content = f.read()
                return content, detected_encoding
    except Exception as ex:  # pylint: disable=broad-except
        raise Exception(f"Error reading file: {str(ex)}")
