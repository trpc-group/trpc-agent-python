# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Prompts for the CodeAct persistence example."""

INSTRUCTION = """
Complete the task in exactly two CodeAct Python cells.

In the first cell:
- Use the available load_numbers tool once to load the requested numbers.
- Save the returned value in a global variable named nums.
- The tool returns range(count), from 0 through count - 1.
- Print len(nums) and doc(nums) so the result can be observed.
- Do not perform the final calculation.
- Do not call return_result.

In the second cell:
- Reuse the existing nums variable from the first cell.
- Do not call the load_numbers tool again and do not recreate nums.
- Perform and verify the requested calculation.
- Ensure the expression describes the exact range.
- Call return_result with the final structured result.
""".strip()
