#!/usr/bin/env python3
"""Sample 01 — SAFE: pure computation, no side effects.

Expected verdict: allow.
"""


def fibonacci(n: int) -> int:
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


if __name__ == "__main__":
    print([fibonacci(i) for i in range(10)])
