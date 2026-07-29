#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""End-to-end checks for the user-facing release documentation contract."""

from __future__ import annotations

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MARKDOWN_LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def _local_markdown_targets(document_path: Path) -> list[Path]:
    """提取 Markdown 中的本地链接并解析为可验证的绝对路径。"""

    document = document_path.read_text(encoding="utf-8")
    targets: list[Path] = []
    for raw_target in MARKDOWN_LINK_PATTERN.findall(document):
        target = raw_target.strip("<>").split("#", maxsplit=1)[0]
        if not target or target.startswith(("http://", "https://", "mailto:")):
            continue
        targets.append((document_path.parent / target).resolve())
    return targets


def test_release_docs_cover_usage_safety_design_risks_and_acceptance() -> None:
    """验证 README 与设计说明覆盖 E2 约定的可执行使用、安全和验收信息。"""

    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    design = (PROJECT_ROOT / "DESIGN.md").read_text(encoding="utf-8")
    design_statement = design.split("## 方案设计说明", maxsplit=1)[1].split(
        "## 规格与交付范围",
        maxsplit=1,
    )[0]
    chinese_character_count = sum("\u4e00" <= character <= "\u9fff" for character in design_statement)

    required_readme_terms = (
        "## 规格依据",
        "DEV_SPEC.md",
        "## 交付物总览",
        "OPERATIONS.md",
        ".env.example",
        ".venv/bin/python",
        "skills/code-review/SKILL.md",
        "code_review/store/models.py",
        "tests/fixtures/diffs/",
        "sample_output/review_report.json",
        "--diff-file",
        "--repo-path",
        "--files",
        "--fixture",
        "--dry-run",
        "--model-mode real",
        "user-query",
        "--log-level INFO",
        "report_files",
        "TRPC_AGENT_API_KEY",
        "TRPC_AGENT_BASE_URL",
        "TRPC_AGENT_MODEL_NAME",
        "skills/code-review/scripts/manifest.json",
        "--sandbox local",
        "1 MiB",
        "2 MiB",
        "公开代理",
        "不证明",
    )
    required_design_topics = (
        "## 规格与交付范围",
        "DEV_SPEC.md",
        "## 交付物与架构映射",
        "OPERATIONS.md",
        "user-query",
        "--log-level INFO",
        "Bash",
        "Skill",
        "沙箱",
        "Filter",
        "监控",
        "数据库",
        "去重",
        "脱敏",
        "安全边界",
    )

    assert all(term in readme for term in required_readme_terms)
    assert all(term in design for term in required_design_topics)
    assert "不能替代官方隐藏样本验收" in readme
    assert "不证明" in readme
    assert "官方隐藏样本待官方验收" in design
    assert 300 <= chinese_character_count <= 500
    assert design.count("|") >= 24
    assert all(f"AC{number}" in readme for number in range(1, 9))
    assert all(f"AC{number}" in design for number in range(1, 9))


def test_release_docs_local_links_resolve() -> None:
    """验证 README 与设计说明中的本地交付物链接都指向真实文件或目录。"""

    document_paths = (PROJECT_ROOT / "README.md", PROJECT_ROOT / "DESIGN.md")
    local_targets = [
        target
        for document_path in document_paths
        for target in _local_markdown_targets(document_path)
    ]

    assert local_targets
    assert all(target.exists() for target in local_targets)


def test_readme_is_primary_and_operations_is_a_detailed_supplement() -> None:
    """验证 README 完整呈现验收入口，同时将命令矩阵和排障细节委托给维护手册。"""

    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    operations = (PROJECT_ROOT / "OPERATIONS.md").read_text(encoding="utf-8")

    required_readme_sections = (
        "## 验收标准与当前证据",
        "## 环境与快速开始",
        "## 四种输入与运行模式",
        "## 运行结果与报告定位",
        "## 最小验证命令",
        "## 2026-07-28 独立 Agent 实测基准",
        "## 文档导航",
        "## 适用场景建议",
    )
    required_acceptance_rows = (
        "| AC1 | 8 条公开 diff",
        "| AC2 | 隐藏样本上高危问题检出率 ≥ 80%，误报率 ≤ 15%",
        "| AC3 | 数据库完整记录 task、sandbox run、finding 和 report",
        "| AC4 | 沙箱执行具备超时和输出大小限制",
        "| AC5 | 敏感信息脱敏检出率 ≥ 95%",
        "| AC6 | dry-run / fake model 模式下完整评审流程耗时 ≤ 2 分钟",
        "| AC7 | 高风险脚本必须先经过 Filter；deny / needs_human_review",
        "| AC8 | 报告包含 findings 摘要、严重级别统计、人工复核项",
    )

    assert all(section in readme for section in required_readme_sections)
    assert all(row in readme for row in required_acceptance_rows)
    assert "不能替代官方隐藏样本验收" in readme
    assert "不证明" in readme
    assert "[`README.md`](README.md)" in operations
    assert "是项目主入口" in operations
    assert "详细维护与 PR 验收补充" in operations
    assert "review_fixture 02_security_simple agent" not in operations


def test_release_docs_explicitly_mark_cube_as_unavailable() -> None:
    """锁定 Cube/E2B 尚未接入 CLI runtime factory 的文档边界，避免把预留枚举误当作可运行后端。"""

    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    operations = (PROJECT_ROOT / "OPERATIONS.md").read_text(encoding="utf-8")
    design = (PROJECT_ROOT / "DESIGN.md").read_text(encoding="utf-8")

    assert "当前示例不支持，不能用于评审" in readme
    assert "未注入 Cube/E2B runtime factory" in readme
    assert "Cube/E2B 当前不可用，不是可执行示例" in operations
    assert "配置错误（退出码 2）" in operations
    assert "请使用 `container` 或显式 `local`" in operations
    assert "CLI 尚未注入 `cube_runtime_factory`" in design
    assert "--sandbox cube --dry-run" not in operations


def test_readme_contains_a_sanitized_real_agent_container_trace_example() -> None:
    """验证 README 给出真实模型与容器链路的脱敏预期终端输出。"""

    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
    required_trace_terms = (
        "### 真实模型 + Container 的预期终端输出",
        "--model-mode real",
        "--sandbox container",
        "Container started: container_id=<container-id>",
        '"tool": "skill_load"',
        '"tool": "skill_run"',
        '"action": "allow"',
        '"event": "pipeline.sandbox_finished"',
        '"entrypoint": "agent"',
        '"skill_tools": ["skill_load", "skill_run"]',
        '"task_id": "<task-id>"',
        '"json": "<output-dir>/review_report.json"',
        "review_real_trace",
        "review_report.json",
    )

    assert all(term in readme for term in required_trace_terms)
    trace_example = readme.split(
        "### 真实模型 + Container 的预期终端输出",
        maxsplit=1,
    )[1].split("## 四种输入与运行模式", maxsplit=1)[0]
    assert re.search(r"container_id=[0-9a-f]{64}", trace_example) is None
    assert re.search(r'"task_id": "review-[0-9a-f]{32}"', trace_example) is None
    assert re.search(r"[A-Za-z]:\\\\", trace_example) is None
