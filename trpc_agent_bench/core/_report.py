"""Turn benchmark JSON files into Markdown and self-contained HTML."""

from __future__ import annotations

import html
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any


def _format_time(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    if seconds <= 0:
        return "0"
    if seconds < 1e-3:
        return f"{seconds * 1e6:.1f} us"
    if seconds < 1:
        return f"{seconds * 1e3:.2f} ms"
    return f"{seconds:.2f} s"


def _format_memory(mebibytes: float | None) -> str:
    if mebibytes is None:
        return "-"
    return f"{mebibytes * 1024:.1f} KiB"


def _format_delta(delta: float | None) -> str:
    if delta is None:
        return "-"
    return f"{delta:+.1f}%"


def _delta(baseline: float | None, target: float | None) -> float | None:
    if baseline is None or target is None or baseline == 0:
        return None
    return (target - baseline) / baseline * 100.0


def _delta_class(delta: float | None) -> str:
    if delta is None or delta == 0:
        return ""
    return "better" if delta < 0 else "worse"


def _number(result: dict[str, Any] | None, field: str) -> float | None:
    if result is None:
        return None
    value = result.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _is_memory(name: str) -> bool:
    return name.startswith("memory")


def _p50(result: dict[str, Any] | None, memory: bool) -> float | None:
    if memory:
        return None
    return _number(result, "median_run_time")


def _p95(result: dict[str, Any] | None, memory: bool) -> float | None:
    if memory:
        return None
    return _number(result, "p95_run_time")


def _memory(result: dict[str, Any] | None) -> float | None:
    if result is None:
        return None
    usages = result.get("memory_usages")
    if isinstance(usages, list) and not usages:
        return None
    value = _number(result, "median_memory_usage")
    if value is None or (value == 0 and not isinstance(usages, list)):
        return None
    return value


def _load(results_dir: Path, targets: list[str] | None = None) -> list[dict[str, Any]]:
    if targets:
        paths = [path for target in targets for path in sorted((results_dir / target).glob("*.json"))]
    else:
        paths = sorted(results_dir.glob("*/*.json"))
    results = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if "result" not in payload:
            continue
        results.append(payload)
    return results


def _rows(
    results: list[dict[str, Any]],
) -> tuple[
        list[str],
        dict[str, dict[str, Any]],
        dict[str, str],
        dict[str, str],
        dict[str, str],
]:
    targets: list[str] = []
    table: dict[str, dict[str, Any]] = defaultdict(dict)
    descriptions: dict[str, str] = {}
    display_names: dict[str, str] = {}
    labels: dict[str, str] = {}
    for payload in results:
        target = payload.get("target") or "unknown"
        if target not in targets:
            targets.append(target)
        result = payload.get("result") or {}
        name = payload.get("name") or result.get("name") or "unknown"
        table[name][target] = result
        display_name = payload.get("display_name") or name
        display_names.setdefault(name, display_name)
        label = payload.get("label") or ""
        if label:
            labels.setdefault(name, label)
        description = payload.get("description") or ""
        if description and not descriptions.get(name):
            descriptions[name] = description
    return targets, table, descriptions, display_names, labels


def _natural_key(name: str) -> list[Any]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name) if part]


_ORDER = (
    ("import_time", 0),
    ("instantiate_agent", 1),
    ("turn", 2),
    ("loop", 3),
    ("memory", 4),
    ("tools_parallel", 6),
    ("tools", 5),
)


def _name_key(name: str) -> tuple[int, int, list[Any]]:
    for prefix, rank in _ORDER:
        if prefix in ("turn", "loop"):
            if not name.startswith(prefix):
                continue
            suffix = name[len(prefix):]
            if suffix and suffix[0].isdigit():
                return rank, 0, _natural_key(name)
            continue
        if name == prefix or name.startswith(f"{prefix}_"):
            return rank, 0, _natural_key(name)
    return 7, 0, _natural_key(name)


def _ordered(table: dict[str, dict[str, Any]], ) -> list[tuple[str, dict[str, Any]]]:
    return sorted(table.items(), key=lambda item: _name_key(item[0]))


def _md_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _html_cell(value: str) -> str:
    return html.escape(value).replace("\n", " ")


def _description(descriptions: dict[str, str], name: str) -> str:
    return descriptions.get(name) or "-"


def _markdown_name(
    name: str,
    display_names: dict[str, str],
    labels: dict[str, str],
) -> str:
    display = _md_cell(display_names.get(name, name))
    label = labels.get(name)
    return f"{display} ({_md_cell(label)})" if label else display


def _html_name(
    name: str,
    display_names: dict[str, str],
    labels: dict[str, str],
) -> str:
    display = _html_cell(display_names.get(name, name))
    label = labels.get(name)
    if not label:
        return display
    return f"{display}<div class='sub'>{_html_cell(label)}</div>"


def _format_value(value: float | None, memory: bool) -> str:
    return _format_memory(value) if memory else _format_time(value)


def _format_p95(value: float | None, memory: bool) -> str:
    return "-" if memory else _format_time(value)


def _with_delta(value: str, delta: float | None) -> str:
    if delta is None:
        return value
    return f"{value} {_format_delta(delta)}"


def _markdown_absolute(
    table: dict[str, dict[str, Any]],
    target: str,
    display_names: dict[str, str],
    labels: dict[str, str],
) -> list[str]:
    lines = [
        "| benchmark | p50 | p95 | memory |",
        "|---|---|---|---|",
    ]
    for name, by_target in _ordered(table):
        memory_only = _is_memory(name)
        result = by_target.get(target)
        lines.append(
            f"| {_markdown_name(name, display_names, labels)} | "
            f"{_format_value(_p50(result, memory_only), memory_only)} | "
            f"{_format_p95(_p95(result, memory_only), memory_only)} | "
            f"{_format_memory(_memory(result))} |", )
    return lines


def _markdown_comparison(
    table: dict[str, dict[str, Any]],
    baseline: str,
    target: str,
    display_names: dict[str, str],
    labels: dict[str, str],
) -> list[str]:
    lines = [
        f"| benchmark | baseline p50 | {_md_cell(target)} p50 | "
        f"baseline p95 | {_md_cell(target)} p95 | "
        f"baseline memory | {_md_cell(target)} memory |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, by_target in _ordered(table):
        memory_only = _is_memory(name)
        base = by_target.get(baseline)
        current = by_target.get(target)
        base_p50 = _p50(base, memory_only)
        current_p50 = _p50(current, memory_only)
        base_p95 = _p95(base, memory_only)
        current_p95 = _p95(current, memory_only)
        base_memory = _memory(base)
        current_memory = _memory(current)
        lines.append(
            f"| {_markdown_name(name, display_names, labels)} | "
            f"{_format_value(base_p50, memory_only)} | "
            f"{_with_delta(_format_value(current_p50, memory_only), _delta(base_p50, current_p50))} | "
            f"{_format_p95(base_p95, memory_only)} | "
            f"{_with_delta(_format_p95(current_p95, memory_only), _delta(base_p95, current_p95))} | "
            f"{_format_memory(base_memory)} | "
            f"{_with_delta(_format_memory(current_memory), _delta(base_memory, current_memory))} |", )
    return lines


def _html_delta(delta: float | None) -> str:
    if delta is None:
        return ""
    css = _delta_class(delta)
    return f" <span class='delta {css}'>{_format_delta(delta)}</span>"


def _html_absolute(
    table: dict[str, dict[str, Any]],
    target: str,
    display_names: dict[str, str],
    labels: dict[str, str],
) -> str:
    rows = []
    for name, by_target in _ordered(table):
        memory_only = _is_memory(name)
        result = by_target.get(target)
        p50 = _p50(result, memory_only)
        p95 = _p95(result, memory_only)
        rows.append(
            f"<tr><td class='benchmark'>{_html_name(name, display_names, labels)}</td>"
            f"<td class='num'>{_format_value(p50, memory_only)}</td>"
            f"<td class='num'>{_format_p95(p95, memory_only)}</td>"
            f"<td class='num'>{_format_memory(_memory(result))}</td></tr>", )
    return ("<div class='table-wrap'><table><thead><tr>"
            "<th class='benchmark'>benchmark</th><th>p50</th><th>p95</th><th>memory</th>"
            f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div>")


def _html_comparison(
    table: dict[str, dict[str, Any]],
    baseline: str,
    target: str,
    display_names: dict[str, str],
    labels: dict[str, str],
) -> str:
    rows = []
    for name, by_target in _ordered(table):
        memory_only = _is_memory(name)
        base = by_target.get(baseline)
        current = by_target.get(target)
        base_p50 = _p50(base, memory_only)
        current_p50 = _p50(current, memory_only)
        base_p95 = _p95(base, memory_only)
        current_p95 = _p95(current, memory_only)
        base_memory = _memory(base)
        current_memory = _memory(current)
        rows.append(
            f"<tr><td class='benchmark'>{_html_name(name, display_names, labels)}</td>"
            f"<td class='num'>{_format_value(base_p50, memory_only)}</td>"
            f"<td class='num target'>{_format_value(current_p50, memory_only)}"
            f"{_html_delta(_delta(base_p50, current_p50))}</td>"
            f"<td class='num'>{_format_p95(base_p95, memory_only)}</td>"
            f"<td class='num target'>{_format_p95(current_p95, memory_only)}"
            f"{_html_delta(_delta(base_p95, current_p95))}</td>"
            f"<td class='num'>{_format_memory(base_memory)}</td>"
            f"<td class='num target'>{_format_memory(current_memory)}"
            f"{_html_delta(_delta(base_memory, current_memory))}</td></tr>", )
    return ("<div class='table-wrap'><table><thead><tr>"
            "<th class='benchmark'>benchmark</th>"
            f"<th>baseline p50</th><th class='target'>{_html_cell(target)} p50</th>"
            f"<th>baseline p95</th><th class='target'>{_html_cell(target)} p95</th>"
            f"<th>baseline memory</th><th class='target'>{_html_cell(target)} memory</th>"
            f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div>")


def build_report(
    results_dir: str | Path,
    targets: list[str] | None = None,
    baseline: str | None = None,
) -> Path:
    directory = Path(results_dir)
    results = _load(directory, targets)
    discovered_targets, table, descriptions, display_names, labels = _rows(results)
    target_names = targets or discovered_targets
    if not target_names:
        raise RuntimeError(f"no benchmark results under {directory}")

    if len(target_names) > 1:
        if baseline is None:
            raise ValueError("baseline is required when comparing multiple targets")
        if baseline not in target_names:
            raise ValueError(f"baseline {baseline!r} is not in targets")
        comparison_targets = [target for target in target_names if target != baseline]
    else:
        if baseline is not None and baseline != target_names[0]:
            raise ValueError("baseline must match the only target when targets has one entry")
        comparison_targets = []

    lines = [
        "# trpc-agent bench report",
        "",
        f"- results: `{directory}`",
        f"- targets: {', '.join(target_names)}",
        "",
        "## Benchmark 说明",
        "",
    ]
    for name, _ in _ordered(table):
        lines.append(f"- `{name}`：{_md_cell(_description(descriptions, name))}")
    lines.append("")

    if comparison_targets:
        for target in comparison_targets:
            lines.extend([f"## {baseline} vs {target}", ""])
            lines.extend(_markdown_comparison(table, baseline, target, display_names, labels), )
            lines.append("")
    else:
        lines.extend([f"## {target_names[0]}", ""])
        lines.extend(_markdown_absolute(table, target_names[0], display_names, labels), )
        lines.append("")

    lines.extend([
        "Notes:",
        "- benchmark 按固定顺序排列，`turn1` / `turn5` / `turn10` 和 `loop1` / `loop5` / `loop10` 分别放在一起；同组内数字按数值比较。",
        "- Benchmark 说明来自 result JSON 的 `description`；自定义 benchmark 未提供时显示 `-`。",
        "- p50/p95 是耗时的中位数/95 分位；memory 是 `median_memory_usage`。",
        "- `-` 表示该 benchmark 没有测这个指标：`import_time` 不测内存，"
        "`memory_per_agent*` 不测耗时。",
        "- 带并发的结果文件名和 `name` 带 `_cN` 后缀；表格里用 `display_name` "
        "和 `concurrency=N` 标签展示。",
    ], )
    if comparison_targets:
        lines.append("- 百分比（跟在 target 数字后面）= (target - baseline) / baseline；"
                     "负数表示 target 更快/更省，正数表示更慢/更多。", )
    markdown = "\n".join(lines) + "\n"
    (directory / "report.md").write_text(markdown, encoding="utf-8")

    description_items = "".join(
        f"<li><code>{_html_cell(name)}</code>：{_html_cell(_description(descriptions, name))}</li>"
        for name, _ in _ordered(table))
    sections = []
    if comparison_targets:
        for target in comparison_targets:
            sections.append(
                f"<section><h2>{_html_cell(baseline or '')} vs {_html_cell(target)}</h2>"
                f"{_html_comparison(table, baseline or '', target, display_names, labels)}</section>", )
    else:
        target = target_names[0]
        sections.append(
            f"<section><h2>{_html_cell(target)}</h2>"
            f"{_html_absolute(table, target, display_names, labels)}</section>", )

    document = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>trpc-agent bench report</title>
<style>
:root {{ color-scheme: light; }}
html, body {{ background: #ffffff; color: #000000; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; line-height: 1.5; }}
h1, h2 {{ color: #000000; }}
.table-wrap {{ overflow-x: auto; margin-bottom: 24px; }}
table {{ border-collapse: collapse; width: 100%; background: #ffffff; }}
th, td {{ border: 1px solid #cccccc; padding: 8px 10px; text-align: left; vertical-align: top; color: #000000; background: #ffffff; }}
th {{ background: #f2f2f2; font-weight: normal; }}
th.benchmark, th.target {{ font-weight: 700; }}
td.benchmark, td.target {{ font-weight: 600; }}
td.benchmark .sub {{ color: #555555; font-size: 0.85em; font-weight: normal; margin-top: 2px; }}
td.num, th.num {{ white-space: nowrap; }}
.delta.better {{ color: #137333; font-weight: 600; }}
.delta.worse {{ color: #b3261e; font-weight: 600; }}
code {{ background: #f2f2f2; color: #000000; padding: 1px 4px; border-radius: 3px; }}
ul.bench-descriptions {{ margin: 0 0 24px 0; padding-left: 22px; }}
ul.bench-descriptions li {{ margin-bottom: 8px; }}
</style></head>
<body><h1>trpc-agent bench report</h1>
<p>results: <code>{html.escape(str(directory))}</code></p>
<h2>Benchmark 说明</h2>
<ul class='bench-descriptions'>{description_items}</ul>
{''.join(sections)}
</body></html>
"""
    (directory / "report.html").write_text(document, encoding="utf-8")
    return directory
