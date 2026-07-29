#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Canonical JSON report validation, persistence payloads and renderers."""

from __future__ import annotations

import html
import json
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from code_review.redaction import contains_plaintext_secret


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_SCHEMA_PATH = _PROJECT_ROOT / "schemas" / "review_report.schema.json"
_SEVERITY_RANK = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}
_FINDING_BUCKETS = ("findings", "needs_human_review")


class ReportValidationError(ValueError):
    """表示报告不满足 canonical schema 或 JSON 数据约束。"""


class ReportSecretLeakError(ReportValidationError):
    """表示报告出口仍包含明文敏感信息，禁止写入。"""


class ReportWriteError(RuntimeError):
    """表示报告不能以原子方式写入，且不会保留半写目标。"""


class ReportRenderer(Protocol):
    """定义从已校验 canonical JSON 扩展到其他报告格式的协议。"""

    def render(self, report: Mapping[str, Any]) -> str:
        """根据 canonical JSON 返回确定性文本，不访问原始输入。"""


AtomicWriter = Callable[[Path, bytes], None]


def _markdown_text(value: Any) -> str:
    """转义不可信文本中的 Markdown 控制符、原始 HTML 和换行。"""

    escaped = html.escape(str(value), quote=False)
    escaped = escaped.replace("\r", " ").replace("\n", " ")
    for character in "\\`*_{}[]()#+-.!|>":
        escaped = escaped.replace(character, f"\\{character}")
    return escaped


def _markdown_code(value: Any) -> str:
    """把不可信标识安全放入 HTML code 元素，避免反引号闭合注入。"""

    escaped = html.escape(str(value), quote=False)
    for character in "`[]()!":
        escaped = escaped.replace(character, f"&#{ord(character)};")
    return f"<code>{escaped}</code>"


def _markdown_identifier(value: Any) -> str:
    """转义结构化标识的 HTML 字符，同时保留可读下划线和连字符。"""

    return html.escape(str(value), quote=False).replace("\r", "").replace(
        "\n",
        "",
    )


@dataclass(frozen=True)
class WrittenReport:
    """保存一次成功输出的路径与已校验 canonical 报告对象。"""

    json_path: Path
    markdown_path: Path
    report: dict[str, Any]


def _canonical_json_bytes(value: Any, *, pretty: bool) -> bytes:
    """将 JSON 兼容对象稳定序列化为 UTF-8 字节，拒绝不可序列化输入。"""

    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            indent=2 if pretty else None,
            separators=None if pretty else (",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ReportValidationError("report must be JSON serializable") from exc
    return (text + "\n").encode("utf-8")


def _stable_value_key(value: Any) -> str:
    """为未知摘要对象生成不泄漏内容的确定性排序键。"""

    return _canonical_json_bytes(value, pretty=False).decode("utf-8")


def _finding_sort_key(finding: Mapping[str, Any]) -> tuple[Any, ...]:
    """按规格定义的严重级别、位置和规则标识稳定排序 finding。"""

    severity = finding.get("severity")
    return (
        _SEVERITY_RANK.get(severity, len(_SEVERITY_RANK)),
        str(finding.get("file", "")),
        finding.get("line", -1),
        str(finding.get("category", "")),
        str(finding.get("rule_id", "")),
        _stable_value_key(finding),
    )


def _normalize_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """复制并稳定排序报告中有顺序语义的集合，保留输入字段供 schema 校验。"""

    serialized = _canonical_json_bytes(report, pretty=False)
    normalized = json.loads(serialized)
    if not isinstance(normalized, dict):
        raise ReportValidationError("report must be a JSON object")

    for name in _FINDING_BUCKETS:
        candidates = normalized.get(name)
        if isinstance(candidates, list) and all(
            isinstance(candidate, Mapping) for candidate in candidates
        ):
            normalized[name] = sorted(candidates, key=_finding_sort_key)

    warnings = normalized.get("warnings")
    if isinstance(warnings, list):
        normalized["warnings"] = sorted(warnings, key=_stable_value_key)

    input_summary = normalized.get("input_summary")
    if isinstance(input_summary, dict) and isinstance(input_summary.get("files"), list):
        input_summary["files"] = sorted(
            input_summary["files"],
            key=lambda item: _stable_value_key(item),
        )

    for summary_name, list_name in (
        ("filter_summary", "events"),
        ("sandbox_summary", "runs"),
    ):
        summary = normalized.get(summary_name)
        if isinstance(summary, dict) and isinstance(summary.get(list_name), list):
            summary[list_name] = sorted(summary[list_name], key=_stable_value_key)

    conclusion = normalized.get("final_conclusion")
    if isinstance(conclusion, dict) and isinstance(
        conclusion.get("recommendations"), list
    ):
        conclusion["recommendations"] = sorted(
            conclusion["recommendations"],
            key=_stable_value_key,
        )
    return normalized


def _write_atomically(path: Path, payload: bytes) -> None:
    """在目标同目录落临时文件后替换目标，失败时删除临时残留。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            temporary_file.write(payload)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


class MarkdownReportRenderer:
    """将 canonical JSON 渲染为固定八段 Markdown 的默认 renderer。"""

    def render(self, report: Mapping[str, Any]) -> str:
        """只读取已校验 JSON 字段，输出确定性且不含原始输入的 Markdown。"""

        lines = [
            "# 自动代码评审报告",
            "",
            f"任务 ID：{_markdown_identifier(report['task_id'])}",
            f"状态：{_markdown_identifier(report['status'])}",
            "",
            "## 输入范围",
        ]
        lines.extend(self._render_input_summary(report["input_summary"]))
        lines.extend(["", "## 1. Findings 摘要"])
        lines.extend(self._render_findings(report["findings"]))
        lines.extend(["", "## 2. 严重级别统计"])
        lines.extend(self._render_distribution(report["metrics"]["severity_distribution"]))
        lines.extend(["", "## 3. 人工复核项"])
        lines.extend(self._render_findings(report["needs_human_review"]))
        lines.extend(["", "## 4. 运行告警"])
        lines.extend(self._render_warnings(report["warnings"]))
        lines.extend(["", "## 5. Filter 拦截摘要"])
        lines.extend(self._render_filter_summary(report["filter_summary"]))
        lines.extend(["", "## 6. 沙箱执行摘要"])
        lines.extend(self._render_sandbox_summary(report["sandbox_summary"]))
        lines.extend(["", "## 7. 监控指标"])
        lines.extend(self._render_metrics(report["metrics"]))
        lines.extend(["", "## 8. 结论与可执行修复建议"])
        lines.extend(self._render_conclusion(report))
        return "\n".join(lines) + "\n"

    def _render_input_summary(self, input_summary: Mapping[str, Any]) -> list[str]:
        """渲染输入来源及每个文件的审查范围。"""

        lines = [
            f"- 来源：{_markdown_identifier(input_summary['source_kind'])}",
            (
                "- 文件数：{file_count}；hunk 数：{hunk_count}；新增：{additions}；删除：{deletions}".format(
                    **input_summary
                )
            ),
        ]
        for item in input_summary["files"]:
            lines.append(
                "- {path}：状态={status}；审查范围：{review_scope}".format(
                    path=_markdown_code(item["path"]),
                    status=_markdown_identifier(item["status"]),
                    review_scope=_markdown_identifier(item["review_scope"]),
                )
            )
        return lines

    def _render_findings(self, findings: Sequence[Mapping[str, Any]]) -> list[str]:
        """渲染一个 finding 桶，并显式展示新侧或旧侧行号。"""

        if not findings:
            return ["- 无。"]
        lines: list[str] = []
        for finding in findings:
            side = "旧侧" if finding.get("line_side", "new") == "old" else "新侧"
            lines.extend(
                [
                    (
                        "- [{severity}] {file}（{side}行 {line}）— {title}"
                    ).format(
                        severity=_markdown_identifier(finding["severity"]),
                        file=_markdown_code(finding["file"]),
                        side=side,
                        line=finding["line"],
                        title=_markdown_text(finding["title"]),
                    ),
                    f"  - 证据：{_markdown_text(finding['evidence'])}",
                    f"  - 建议：{_markdown_text(finding['recommendation'])}",
                ]
            )
        return lines

    def _render_distribution(self, distribution: Mapping[str, Any]) -> list[str]:
        """按严重级别固定顺序渲染计数分布。"""

        if not distribution:
            return ["- 无。"]
        return [
            f"- {severity}：{distribution[severity]}"
            for severity in sorted(
                distribution,
                key=lambda severity: (
                    _SEVERITY_RANK.get(severity, len(_SEVERITY_RANK)),
                    severity,
                ),
            )
        ]

    def _render_warnings(self, warnings: Sequence[Mapping[str, Any]]) -> list[str]:
        """渲染运行或治理告警，且不将其混入 finding。"""

        if not warnings:
            return ["- 无。"]
        return [
            "- [{code}] {message}".format(
                code=_markdown_identifier(warning["code"]),
                message=_markdown_text(warning["message"]),
            )
            for warning in warnings
        ]

    def _render_filter_summary(self, summary: Mapping[str, Any]) -> list[str]:
        """渲染 Filter 三类决策计数和脱敏事件数量。"""

        return [
            "- allow={allow_count}；deny={deny_count}；needs_human_review={needs_human_review_count}".format(
                **summary
            ),
            f"- 事件数：{len(summary['events'])}",
        ]

    def _render_sandbox_summary(self, summary: Mapping[str, Any]) -> list[str]:
        """渲染沙箱 runtime、执行次数和脱敏运行摘要数量。"""

        return [
            f"- runtime：{summary['runtime_type']}",
            f"- 执行次数：{summary['run_count']}；运行摘要数：{len(summary['runs'])}",
        ]

    def _render_metrics(self, metrics: Mapping[str, Any]) -> list[str]:
        """渲染允许持久化的指标计数与耗时摘要。"""

        return [
            f"- 总耗时：{metrics['total_duration_ms']} ms",
            f"- 沙箱耗时：{metrics['sandbox_duration_ms']} ms",
            f"- 工具调用：{metrics['tool_call_count']}；沙箱运行：{metrics['sandbox_run_count']}",
            f"- warnings：{metrics['warning_count']}；suppressed：{metrics['suppressed_count']}",
        ]

    def _render_conclusion(self, report: Mapping[str, Any]) -> list[str]:
        """按 finding 严重级别顺序合并结论与可执行修复建议。"""

        conclusion = report["final_conclusion"]
        recommendations: list[str] = []
        for finding in report["findings"]:
            recommendation = finding["recommendation"]
            if recommendation not in recommendations:
                recommendations.append(recommendation)
        for recommendation in conclusion["recommendations"]:
            if recommendation not in recommendations:
                recommendations.append(recommendation)
        lines = [f"- 摘要：{_markdown_text(conclusion['summary'])}"]
        if recommendations:
            lines.extend(
                f"{index}. {_markdown_text(recommendation)}"
                for index, recommendation in enumerate(recommendations, start=1)
            )
        else:
            lines.append("- 无额外修复建议。")
        return lines


class CanonicalReportWriter:
    """校验、冻结、原子写入 canonical JSON，并从其渲染 Markdown。"""

    def __init__(
        self,
        *,
        schema_path: Path | None = None,
        renderer: ReportRenderer | None = None,
        atomic_writer: AtomicWriter | None = None,
    ) -> None:
        """加载 schema 并允许注入 renderer 或原子写入器用于扩展和测试。"""

        selected_schema_path = schema_path or _DEFAULT_SCHEMA_PATH
        try:
            schema = json.loads(selected_schema_path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
        except (OSError, json.JSONDecodeError, SchemaError) as exc:
            raise ReportValidationError("report schema is unavailable or invalid") from exc
        self._validator = Draft202012Validator(schema)
        self._renderer = renderer or MarkdownReportRenderer()
        self._atomic_writer = atomic_writer or _write_atomically

    def validate(self, report: Mapping[str, Any]) -> dict[str, Any]:
        """稳定化并验证报告 schema，随后阻止任何明文敏感信息离开任务域。"""

        canonical = _normalize_report(report)
        errors = sorted(
            self._validator.iter_errors(canonical),
            key=lambda error: tuple(str(item) for item in error.absolute_path),
        )
        if errors:
            location = ".".join(str(item) for item in errors[0].absolute_path)
            location = location or "root"
            raise ReportValidationError(
                f"canonical report does not match schema at {location}"
            )
        if contains_plaintext_secret(canonical):
            raise ReportSecretLeakError(
                "canonical report contains a plaintext secret"
            )
        return canonical

    def write(
        self,
        report: Mapping[str, Any],
        output_dir: Path,
    ) -> WrittenReport:
        """写入 JSON 后只从其 canonical 对象渲染 Markdown，两个出口均原子替换。"""

        canonical = self.validate(report)
        json_path = output_dir / "review_report.json"
        markdown_path = output_dir / "review_report.md"
        self._write(json_path, _canonical_json_bytes(canonical, pretty=True))

        markdown = self._renderer.render(canonical)
        if contains_plaintext_secret(markdown):
            raise ReportSecretLeakError(
                "rendered report contains a plaintext secret"
            )
        self._write(markdown_path, markdown.encode("utf-8"))
        return WrittenReport(
            json_path=json_path,
            markdown_path=markdown_path,
            report=canonical,
        )

    def to_store_payload(self, report: Mapping[str, Any]) -> dict[str, Any]:
        """从同一 canonical 对象派生 cr_report 写入载荷，避免重新计算统计。"""

        canonical = self.validate(report)
        return {
            "task_id": canonical["task_id"],
            "schema_version": canonical["schema_version"],
            "rule_pack_version": canonical["rule_pack_version"],
            "config_digest": canonical["config_digest"],
            "input_sha256": canonical["input_sha256"],
            "summary": {
                "status": canonical["status"],
                "input_summary": canonical["input_summary"],
                "final_conclusion": canonical["final_conclusion"],
                "finding_count": len(canonical["findings"]),
                "needs_human_review_count": len(
                    canonical["needs_human_review"]
                ),
                "warning_count": len(canonical["warnings"]),
                "suppressed_count": canonical["suppressed"]["count"],
            },
            "severity_stats": canonical["metrics"]["severity_distribution"],
            "filter_summary": canonical["filter_summary"],
            "sandbox_summary": canonical["sandbox_summary"],
            "metrics": canonical["metrics"],
            "report": canonical,
        }

    def _write(self, path: Path, payload: bytes) -> None:
        """调用可注入原子写入器，并把底层异常收敛为不含路径的错误。"""

        try:
            self._atomic_writer(path, payload)
        except OSError as exc:
            raise ReportWriteError("failed to atomically write report output") from exc


__all__ = [
    "CanonicalReportWriter",
    "MarkdownReportRenderer",
    "ReportRenderer",
    "ReportSecretLeakError",
    "ReportValidationError",
    "ReportWriteError",
    "WrittenReport",
]
