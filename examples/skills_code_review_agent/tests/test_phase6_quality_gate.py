"""Phase 6 quality-gate tests for PR readiness."""

from __future__ import annotations

import json
from pathlib import Path

from examples.skills_code_review_agent.agent.agent import run_review_task
from examples.skills_code_review_agent.agent.config import ReviewAgentConfig

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_all_public_fixtures_generate_reports(tmp_path: Path) -> None:
    """All public fixtures should complete and generate report artifacts."""

    fixture_paths = sorted(FIXTURES_DIR.glob("*.diff"))
    assert len(fixture_paths) == 8

    for fixture_path in fixture_paths:
        out_dir = tmp_path / fixture_path.stem
        db_path = out_dir / "review.db"
        config = ReviewAgentConfig(
            fixture_path=str(fixture_path),
            output_dir=out_dir,
            db_path=db_path,
            dry_run=True,
            fake_model=True,
        )

        task, report = run_review_task(config)

        assert task.status.value == "completed"
        assert report.task_id == task.task_id
        assert (out_dir / "review_report.json").exists()
        assert (out_dir / "review_report.md").exists()

        payload = json.loads((out_dir / "review_report.json").read_text(encoding="utf-8"))
        assert payload["task_id"] == task.task_id
        assert "monitoring_summary" in payload
