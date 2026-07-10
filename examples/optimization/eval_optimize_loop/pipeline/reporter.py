from __future__ import annotations

import json
from pathlib import Path

from .models import OptimizationReport


def write_reports(report: OptimizationReport, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "optimization_report.json"
    markdown_path = output_dir / "optimization_report.md"
    json_path.write_text(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = ["# Optimization Report", "", f"Selected candidate: {report.selected_candidate_id or 'none'}", "", "## Candidates", ""]
    for candidate in report.candidates:
        status = "ACCEPT" if candidate.accepted else "REJECT"
        lines.append(f"- {candidate.candidate_id}: {status} — {'; '.join(candidate.reasons)}")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path
