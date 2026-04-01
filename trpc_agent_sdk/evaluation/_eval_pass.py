# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Pass@k and pass^k: basic formulas. Use with (n, c) from AgentEvaluator.parse_pass_nc."""

from __future__ import annotations

import math


def pass_at_k(n: int, c: int, k: int) -> float:
    """Probability that at least one of k attempts succeeds. pass@k = 1 - C(n-c,k)/C(n,k)."""
    if n < 0:
        raise ValueError("n must be >= 0")
    if k <= 0:
        raise ValueError("k must be >= 1")
    if c < 0:
        raise ValueError("c must be >= 0")
    if c > n:
        raise ValueError("c cannot exceed n")
    if k > n:
        raise ValueError("k cannot exceed n")
    if c == 0:
        return 0.0
    if n - c < k:
        return 1.0
    nf, cf, kf = float(n), float(c), float(k)
    a = math.lgamma(nf - cf + 1)
    b = math.lgamma(nf - kf + 1)
    d = math.lgamma(nf - cf - kf + 1)
    e = math.lgamma(nf + 1)
    log_p = a + b - d - e
    return -math.expm1(log_p)


def pass_hat_k(n: int, c: int, k: int) -> float:
    """Probability that all k consecutive runs succeed. pass^k = (c/n)^k."""
    if n <= 0:
        raise ValueError("n must be > 0")
    if k <= 0:
        raise ValueError("k must be >= 1")
    if c < 0:
        raise ValueError("c must be >= 0")
    if c > n:
        raise ValueError("c cannot exceed n")
    if c == 0:
        return 0.0
    if c == n:
        return 1.0
    p = float(c) / float(n)
    return math.exp(float(k) * math.log(p))
