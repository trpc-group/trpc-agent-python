# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Shared test helpers: run the FULL pipeline on a fixture, return everything."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from typing import Dict
from typing import Optional

from codereview.config import ReviewConfig
from codereview.config import SandboxConfig
from codereview.inputs import from_fixture
from codereview.pipeline import ReviewPipeline
from codereview.pipeline import ReviewResult
from codereview.store import SqlReviewStore


@dataclass
class FixtureRun:
    result: ReviewResult
    report: Dict[str, Any]
    report_md: str
    store: SqlReviewStore
    db_path: str


async def run_fixture(name: str, tmp_path, *, force_fail: bool = False,
                      timeout_sec: float = 30.0, max_output_bytes: int = 64_000,
                      model_mode: str = "fake",
                      config: Optional[ReviewConfig] = None) -> FixtureRun:
    """Run the whole pipeline (local sandbox, sqlite in tmp_path) on a fixture.

    Caller MUST ``await run.store.close()`` when done (or use ``finally``).
    """
    db_path = os.path.join(str(tmp_path), "review.db")
    out_dir = os.path.join(str(tmp_path), "out")
    if config is None:
        config = ReviewConfig(
            db_url=f"sqlite+aiosqlite:///{db_path}",
            out_dir=out_dir,
            model_mode=model_mode,
            sandbox=SandboxConfig(
                runtime_kind="local",
                timeout_sec=timeout_sec,
                max_output_bytes=max_output_bytes,
                work_root=os.path.join(str(tmp_path), "ws"),
                force_fail=force_fail,
            ),
        )
    store = SqlReviewStore(config.db_url)
    await store.initialize()
    try:
        result = await ReviewPipeline(store, config).run(from_fixture(name))
    except Exception:
        await store.close()
        raise
    with open(result.report_paths["json"], "r", encoding="utf-8") as fh:
        report = json.load(fh)
    with open(result.report_paths["markdown"], "r", encoding="utf-8") as fh:
        report_md = fh.read()
    return FixtureRun(result=result, report=report, report_md=report_md,
                      store=store, db_path=db_path)


def findings_by_category(report: Dict[str, Any], bucket: str = "findings") -> Dict[str, list]:
    grouped: Dict[str, list] = {}
    for finding in report.get(bucket, []):
        grouped.setdefault(finding["category"], []).append(finding)
    return grouped
