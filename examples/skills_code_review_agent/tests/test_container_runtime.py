# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Opt-in container-runtime e2e test. Enable with CR_CONTAINER_TESTS=1."""
import os
import shutil
from pathlib import Path

import pytest

from review.pipeline import ReviewOptions, run_review

EXAMPLE_ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(
    not (shutil.which("docker") and os.getenv("CR_CONTAINER_TESTS") == "1"),
    reason="docker not available or CR_CONTAINER_TESTS != 1")


async def test_container_review_security_fixture(tmp_path):
    result = await run_review(ReviewOptions(
        diff_text=(EXAMPLE_ROOT / "fixtures" / "security_eval.diff").read_text(),
        input_type="fixture", input_ref="security_eval.diff",
        runtime="container", dry_run=True,
        db_url=f"sqlite:///{tmp_path}/cr.db",
        output_dir=str(tmp_path / "out")))
    assert result.report["conclusion"] == "blocked"
    assert any(f["category"] == "security" for f in result.report["findings"])
