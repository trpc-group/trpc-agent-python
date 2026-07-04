# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Replay consistency harness for cross-backend Session / Memory / Summary verification.

== 设计说明 / Design Notes ==

一、归一化策略 / Normalization Strategy
    不同后端在时间戳精度（float vs DB 整数）、自动生成 ID（event_id、
    invocation_id）、JSON key 顺序、空值表示上存在差异。Normalizer 提供五种
    归一化策略：(1) 时间戳四舍五入到 3 位小数；(2) 自动生成 ID 替换为占位符
    "<auto>"；(3) dict key 按字母排序；(4) None / "" / [] / {} 统一为 None；
    (5) 摘要文本合并多余空白并转换 Unicode 标点为 ASCII。

    Different backends vary in timestamp precision (float vs DB integer),
    auto-generated IDs (event_id, invocation_id), JSON key ordering, and
    null/empty representations. The Normalizer applies five strategies:
    (1) timestamps rounded to 3 decimal places; (2) auto-generated IDs
    replaced with the placeholder "<auto>"; (3) dict keys sorted
    alphabetically; (4) None, "", [], {} unified to None; (5) summary text
    whitespace-normalized and Unicode punctuation converted to ASCII.

二、摘要比较策略 / Summary Comparison Strategy
    分三层比较。第一层（存储元数据）：session_id、original_event_count、
    compressed_event_count 必须严格相等，任何差异视为 unallowed diff。
    第二层（内容语义）：summary_text 经归一化后比较。第三层（非业务元数据）：
    summary_timestamp 作为 allowed_diff。三类关键问题保证 100% 检出率：
    summary_loss（一端有摘要另一端缺失）、summary_ownership_error（session_id
    归属错误）、summary_overwrite_error（original_event_count 不一致，说明
    覆盖不完整）。

    Three layers. Layer 1 (storage metadata): session_id, original_event_count,
    compressed_event_count — strict equality, any mismatch is unallowed.
    Layer 2 (content semantics): summary_text compared after normalization.
    Layer 3 (non-business metadata): summary_timestamp is an allowed_diff.
    Three critical issues are guaranteed 100% detection: summary_loss (one
    backend has a summary, another does not), summary_ownership_error
    (session_id mismatch), and summary_overwrite_error (original_event_count
    mismatch, indicating incomplete overwrite).

三、允许差异 / Allowed Differences
    19 条基于 fnmatch 的字段级规则（如 "*.timestamp"、"*.id"），每条规则
    包含书面的合理性说明。不采用全局忽略策略——每个差异必须匹配显式规则，
    否则报告为 unallowed diff。

    19 field-level rules using fnmatch patterns (e.g., "*.timestamp", "*.id").
    Each rule has a documented justification. No blanket ignore — every diff
    must match an explicit rule or be reported as unallowed.

四、后端接入方式 / Backend Integration
    三级控制：(1) 命令行参数（--run-sql、--run-redis、--run-integration）；
    (2) 环境变量（TRPC_TEST_REDIS_URL、TRPC_TEST_RUN_SQL）；
    (3) CI 自动检测（$CI）。SQL 默认使用 SQLite 内存库，无需外部数据库；
    Redis 需配置 TRPC_TEST_REDIS_URL。后端不可用时调用 pytest.skip()，
    不会导致测试失败。

    Three-tier control: (1) CLI flags (--run-sql, --run-redis, --run-integration);
    (2) environment variables (TRPC_TEST_REDIS_URL, TRPC_TEST_RUN_SQL);
    (3) CI auto-detection ($CI). SQL uses SQLite in-memory by default,
    requiring no external database. Redis requires TRPC_TEST_REDIS_URL.
    Unavailable backends call pytest.skip() and never cause test failures.
"""

from .replay_loader import ReplayLoader
from .backend_executor import BackendExecutor
from .snapshot import BackendSnapshot
from .normalizer import Normalizer
from .comparator import Comparator
from .diff_report import DiffReport

__all__ = [
    "ReplayLoader",
    "BackendExecutor",
    "BackendSnapshot",
    "Normalizer",
    "Comparator",
    "DiffReport",
]