"""Report generation: descriptions, baseline deltas, and table shape."""

from __future__ import annotations

import json
import re
from pathlib import Path

from trpc_agent_bench.bench._report import build_report


def _write_result(
    root: Path,
    *,
    target: str,
    name: str,
    description: str = "",
    median_run_time: float = 0.0,
    p95_run_time: float = 0.0,
    median_memory_usage: float = 0.0,
    display_name: str = "",
    label: str = "",
) -> None:
    directory = root / target
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "name": name,
        "display_name": display_name or name,
        "label": label,
        "description": description,
        "target": target,
        "result": {
            "run_times": [median_run_time] if median_run_time else [],
            "memory_usages": [median_memory_usage] if median_memory_usage else [],
            "median_run_time": median_run_time,
            "p95_run_time": p95_run_time,
            "median_memory_usage": median_memory_usage,
        },
    }
    (directory / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")


def _markdown_cell_counts(report: Path) -> list[int]:
    counts = []
    for line in report.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        counts.append(line.strip().strip("|").count("|") + 1)
    return counts


def _markdown_benchmark_names(markdown: str) -> list[str]:
    names = []
    for line in markdown.splitlines():
        if not line.startswith("| "):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not cells or cells[0] == "benchmark":
            continue
        names.append(cells[0])
    return names


def _html_header_count(document: str) -> int:
    thead = re.search(r"<thead>(.*?)</thead>", document, re.S)
    assert thead is not None
    return len(re.findall(r"<th\b", thead.group(1)))


def _html_row_counts(document: str) -> list[int]:
    tbody = re.search(r"<tbody>(.*?)</tbody>", document, re.S)
    assert tbody is not None
    rows = re.findall(r"<tr>(.*?)</tr>", tbody.group(1), re.S)
    return [row.count("<td") for row in rows]


def test_multi_target_report_has_descriptions_and_deltas(tmp_path: Path) -> None:
    _write_result(
        tmp_path,
        target="hermes_v1",
        name="import_time",
        description="新进程 import 耗时。",
        median_run_time=5.23,
        p95_run_time=5.28,
    )
    _write_result(
        tmp_path,
        target="hermes_v2",
        name="import_time",
        description="新进程 import 耗时。",
        median_run_time=4.28,
        p95_run_time=5.17,
    )
    _write_result(
        tmp_path,
        target="hermes_v1",
        name="run_agent",
        description="单轮文本 turn。",
        median_run_time=0.1,
        p95_run_time=0.12,
    )
    _write_result(
        tmp_path,
        target="hermes_v2",
        name="run_agent",
        description="单轮文本 turn。",
        median_run_time=0.03,
        p95_run_time=0.04,
    )
    _write_result(
        tmp_path,
        target="hermes_v1",
        name="memory_per_agent",
        description="每个常驻 agent 占用的内存。",
        median_memory_usage=0.0048,
    )
    _write_result(
        tmp_path,
        target="hermes_v2",
        name="memory_per_agent",
        description="每个常驻 agent 占用的内存。",
        median_memory_usage=0.0085,
    )

    build_report(tmp_path, ["hermes_v1", "hermes_v2"], baseline="hermes_v1")

    counts = _markdown_cell_counts(tmp_path / "report.md")
    assert set(counts) == {7}, counts

    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "| group |" not in markdown
    assert "| extra |" not in markdown
    assert "| 说明 |" not in markdown
    assert "## hermes_v1 vs hermes_v2" in markdown
    assert ("| benchmark | baseline p50 | hermes_v2 p50 | "
            "baseline p95 | hermes_v2 p95 | "
            "baseline memory | hermes_v2 memory |") in markdown
    assert "单轮文本 turn。" in markdown
    assert "-18.2%" in markdown
    assert "-70.0%" in markdown
    assert "+77.1%" in markdown
    assert _markdown_benchmark_names(markdown) == sorted(
        _markdown_benchmark_names(markdown),
        key=str.lower,
    )

    memory_row = next(line for line in markdown.splitlines() if line.startswith("| ") and "memory_per_agent" in line)
    assert memory_row.endswith("| - | - | - | - | 4.9 KiB | 8.7 KiB +77.1% |")

    document = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "color-scheme: light" in document
    assert "background: #ffffff" in document
    assert "color: #000000" in document
    assert "<th>group</th>" not in document
    assert "<th>extra</th>" not in document
    assert "class='bar'" not in document
    assert "th { background: #f2f2f2; font-weight: normal; }" in document
    assert "th.benchmark, th.target { font-weight: 700; }" in document
    assert "td.benchmark, td.target { font-weight: 600; }" in document
    assert "td.extra" not in document
    assert "<th class='benchmark'>benchmark</th>" in document
    assert "<th class='target'>hermes_v2 p50</th>" in document
    assert "<td class='benchmark'>" in document
    assert "<td class='num target'>" in document
    assert document.count("<table>") == 1
    assert _html_header_count(document) == 7
    assert set(_html_row_counts(document)) == {7}
    assert "单轮文本 turn。" in document
    assert "1.77x" not in document


def test_single_target_report_has_description_and_no_baseline(tmp_path: Path) -> None:
    _write_result(
        tmp_path,
        target="hermes_v2",
        name="run_agent",
        description="单轮文本 turn。",
        median_run_time=0.03,
        p95_run_time=0.04,
    )
    _write_result(
        tmp_path,
        target="hermes_v2",
        name="memory_per_agent",
        description="每个常驻 agent 占用的内存。",
        median_memory_usage=0.0085,
    )

    build_report(tmp_path, ["hermes_v2"])

    counts = _markdown_cell_counts(tmp_path / "report.md")
    assert set(counts) == {4}, counts
    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "| benchmark | p50 | p95 | memory |" in markdown
    assert "baseline" not in markdown
    assert "单轮文本 turn。" in markdown

    document = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert _html_header_count(document) == 4
    assert set(_html_row_counts(document)) == {4}
    assert "单轮文本 turn。" in document


def test_baseline_can_be_any_target_and_creates_one_table_per_comparison(tmp_path: Path, ) -> None:
    for target, median in (("target_a", 0.1), ("target_b", 0.05), ("target_c", 0.2)):
        _write_result(
            tmp_path,
            target=target,
            name="run_agent",
            description="单轮文本 turn。",
            median_run_time=median,
            p95_run_time=median * 1.2,
        )

    build_report(tmp_path, ["target_a", "target_b", "target_c"], baseline="target_b")

    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "## target_b vs target_a" in markdown
    assert "## target_b vs target_c" in markdown
    assert "## target_b vs baseline" not in markdown
    assert "+100.0%" in markdown
    assert "+300.0%" in markdown

    document = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert document.count("<table>") == 2


def test_missing_description_and_missing_target_show_dash(tmp_path: Path) -> None:
    _write_result(
        tmp_path,
        target="hermes_v1",
        name="run_agent",
        median_run_time=0.1,
        p95_run_time=0.12,
    )
    _write_result(
        tmp_path,
        target="hermes_v1",
        name="import_time",
        median_run_time=5.23,
        p95_run_time=5.28,
    )
    _write_result(
        tmp_path,
        target="hermes_v2",
        name="run_agent",
        median_run_time=0.03,
        p95_run_time=0.04,
    )

    build_report(tmp_path, ["hermes_v1", "hermes_v2"], baseline="hermes_v1")

    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    import_row = next(line for line in markdown.splitlines() if "| import_time |" in line)
    assert "| import_time | 5.23 s | - | 5.28 s | - | - | - |" in import_row
    run_row = next(line for line in markdown.splitlines() if "| run_agent |" in line)
    assert ("| run_agent | 100.00 ms | 30.00 ms -70.0% | "
            "120.00 ms | 40.00 ms -66.7% | - | - |") in run_row


def test_turn_is_time_and_memory_and_stray_summary_is_ignored(tmp_path: Path) -> None:
    _write_result(
        tmp_path,
        target="hermes_v1",
        name="turn10",
        median_run_time=0.1,
        p95_run_time=0.12,
        median_memory_usage=0.5,
    )
    _write_result(
        tmp_path,
        target="hermes_v2",
        name="turn10",
        median_run_time=0.05,
        p95_run_time=0.06,
        median_memory_usage=0.7,
    )
    stray = tmp_path / "quick"
    stray.mkdir()
    (stray / "summary.json").write_text(json.dumps({"results": []}), encoding="utf-8")

    build_report(tmp_path, ["hermes_v1", "hermes_v2"], baseline="hermes_v1")

    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    row = next(line for line in markdown.splitlines() if line.startswith("| ") and "turn10" in line)
    assert "ms" in row
    assert "KiB" in row
    assert "-50.0%" in row
    assert "unknown" not in markdown


def test_report_renders_concurrency_sublabel(tmp_path: Path) -> None:
    _write_result(
        tmp_path,
        target="hermes_v2",
        name="turn1_redis_c8",
        display_name="turn1_redis",
        label="concurrency=8",
        description="单轮 Redis。",
        median_run_time=0.03,
        p95_run_time=0.04,
    )

    build_report(tmp_path, ["hermes_v2"])

    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "| turn1_redis (concurrency=8) |" in markdown

    document = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "<div class='sub'>concurrency=8</div>" in document


def test_turn_benchmarks_are_ordered_by_turn_count(tmp_path: Path) -> None:
    for name in ("turn10", "turn1", "turn5"):
        _write_result(
            tmp_path,
            target="hermes_v2",
            name=name,
            median_run_time=0.1,
            p95_run_time=0.12,
        )

    build_report(tmp_path, ["hermes_v2"])

    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert _markdown_benchmark_names(markdown) == ["turn1", "turn5", "turn10"]


def test_loop_benchmarks_follow_turns_and_sort_by_call_count(tmp_path: Path) -> None:
    for name in ("loop10", "turn5", "loop1", "turn1", "loop5"):
        _write_result(
            tmp_path,
            target="hermes_v2",
            name=name,
            median_run_time=0.1,
            p95_run_time=0.12,
        )

    build_report(tmp_path, ["hermes_v2"])

    markdown = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert _markdown_benchmark_names(markdown) == [
        "turn1",
        "turn5",
        "loop1",
        "loop5",
        "loop10",
    ]
