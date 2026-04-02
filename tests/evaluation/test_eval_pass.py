# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for pass@k and pass^k (_eval_pass)."""

import pytest

import trpc_agent_sdk.runners  # noqa: F401

from trpc_agent_sdk.evaluation import pass_at_k
from trpc_agent_sdk.evaluation import pass_hat_k


class TestPassAtK:
    """Test suite for pass_at_k."""

    def test_c_zero_returns_zero(self):
        """Test c=0 returns 0.0."""
        assert pass_at_k(5, 0, 1) == 0.0

    def test_n_minus_c_less_than_k_returns_one(self):
        """Test when n - c < k returns 1.0."""
        assert pass_at_k(3, 2, 2) == 1.0

    def test_valid_input(self):
        """Test valid computation."""
        p = pass_at_k(10, 5, 3)
        assert 0 <= p <= 1
        assert isinstance(p, float)

    def test_n_negative_raises(self):
        """Test n < 0 raises ValueError."""
        with pytest.raises(ValueError):
            pass_at_k(-1, 0, 1)

    def test_k_zero_raises(self):
        """Test k <= 0 raises ValueError."""
        with pytest.raises(ValueError):
            pass_at_k(5, 2, 0)

    def test_c_negative_raises(self):
        """Test c < 0 raises ValueError."""
        with pytest.raises(ValueError):
            pass_at_k(5, -1, 1)

    def test_c_exceeds_n_raises(self):
        """Test c > n raises ValueError."""
        with pytest.raises(ValueError):
            pass_at_k(5, 6, 1)

    def test_k_exceeds_n_raises(self):
        """Test k > n raises ValueError."""
        with pytest.raises(ValueError):
            pass_at_k(5, 2, 6)


class TestPassHatK:
    """Test suite for pass_hat_k."""

    def test_c_zero_returns_zero(self):
        """Test c=0 returns 0.0."""
        assert pass_hat_k(5, 0, 1) == 0.0

    def test_c_equals_n_returns_one(self):
        """Test c=n returns 1.0."""
        assert pass_hat_k(5, 5, 3) == 1.0

    def test_valid_input(self):
        """Test pass_hat_k = (c/n)^k."""
        assert pass_hat_k(10, 5, 1) == 0.5
        assert pass_hat_k(10, 5, 2) == 0.25

    def test_n_zero_raises(self):
        """Test n <= 0 raises ValueError."""
        with pytest.raises(ValueError):
            pass_hat_k(0, 0, 1)

    def test_k_zero_raises(self):
        """Test k <= 0 raises ValueError."""
        with pytest.raises(ValueError):
            pass_hat_k(5, 2, 0)

    def test_c_exceeds_n_raises(self):
        """Test c > n raises ValueError."""
        with pytest.raises(ValueError):
            pass_hat_k(5, 6, 1)
