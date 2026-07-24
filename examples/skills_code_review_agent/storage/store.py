# storage/store.py - ReviewStore 存储层实现（验收3 按task查 + 验收5 落库脱敏）
import sqlite3
import json
import uuid
import os
import hashlib
from datetime import datetime
from typing import Optional

from agent.models import ReviewReport
from agent.redaction import redact_text


class ReviewStore:
    """代码审查报告存储层（SQLite 七表 + 真迁移 + 落库脱敏）"""

    def __init__(self, db_url: str = "sqlite:///review.db"):
        """初始化存储层

        Args:
            db_url: 数据库连接 URL，格式：sqlite:///path/to/db.db
        """
        self.path = db_url.split("///")[-1]
        self._init()

    def _init(self):
        """初始化数据库：创建目录、执行 schema.sql、运行迁移"""
        # 确保目录存在
        db_dir = os.path.dirname(self.path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)

        # 执行 schema.sql
        schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
        conn = sqlite3.connect(self.path)
        with open(schema_path, "r", encoding="utf-8") as f:
            conn.executescript(f.read())

        # 运行迁移
        from storage.migrations import run_migrations
        run_migrations(conn)

        conn.commit()
        conn.close()

    def start_task(self, repo: str, scope: str) -> str:
        """启动新的代码审查任务

        Args:
            repo: 仓库 URL
            scope: 审查范围（分支/提交等）

        Returns:
            task_id: 任务 ID（UUID）
        """
        task_id = str(uuid.uuid4())
        created_at = datetime.utcnow().isoformat()

        conn = sqlite3.connect(self.path)
        conn.execute(
            "INSERT INTO review_tasks (task_id, status, repository, scope, created_at) "
            "VALUES (?, ?, ?, ?, ?)", (task_id, "running", repo, scope, created_at))
        conn.commit()
        conn.close()

        return task_id

    def save(self, report: ReviewReport):
        """保存完整审查报告（单事务幂等 + 落库前脱敏）

        验收5 命门：所有可能含密文的列在写前都调用 redact_text

        Args:
            report: 审查报告对象
        """
        conn = sqlite3.connect(self.path)
        try:
            # 开启事务
            conn.execute("BEGIN TRANSACTION")

            # 1. 删除旧数据（幂等：先删后插）
            self._delete_task_data(conn, report.task_id)

            # 2. 插入 input_diffs（如果有的话）
            self._insert_input_diffs(conn, report)

            # 3. 插入 sandbox_runs（落库前脱敏 stdout/stderr）
            self._insert_sandbox_runs(conn, report)

            # 4. 插入 filter_decisions（落库前脱敏 reason/command）
            self._insert_filter_decisions(conn, report)

            # 5. 插入 findings（落库前脱敏 title/evidence/recommendation）
            self._insert_findings(conn, report)

            # 6. 插入 monitoring_summaries
            self._insert_monitoring_summary(conn, report)

            # 7. 插入 review_reports
            self._insert_review_report(conn, report)

            # 8. 更新 review_tasks 状态
            completed_at = datetime.utcnow().isoformat()
            conn.execute(
                "UPDATE review_tasks "
                "SET status=?, conclusion=?, total_duration_ms=?, completed_at=? "
                "WHERE task_id=?",
                (report.status, report.conclusion, report.monitoring.total_duration_ms, completed_at, report.task_id))

            conn.commit()
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            conn.close()

    def _delete_task_data(self, conn: sqlite3.Connection, task_id: str):
        """删除任务的所有关联数据（为幂等插入做准备）"""
        # 由于外键 ON DELETE CASCADE，只需删除 review_tasks 的关联数据
        # 但不能删除 task_id 本身，因为 status 还在更新
        tables = [
            "input_diffs", "sandbox_runs", "filter_decisions", "findings", "monitoring_summaries", "review_reports"
        ]
        for table in tables:
            conn.execute(f"DELETE FROM {table} WHERE task_id=?", (task_id, ))

    def _insert_input_diffs(self, conn: sqlite3.Connection, report: ReviewReport):
        """插入输入差异表（如果有 input_summary）

        验收3 字段完整性：补全 digest 和 files_json 列
        """
        if hasattr(report, "input_summary") and report.input_summary:
            # 脱敏摘要
            redacted_summary, _ = redact_text(report.input_summary)

            # 计算 digest（对脱敏后的摘要计算 SHA256）
            digest = hashlib.sha256(redacted_summary.encode()).hexdigest()

            # 构造 files_json（从 report 的文件信息提取变更文件列表）
            files_changed = []
            if hasattr(report, "findings") and report.findings:
                for finding in report.findings:
                    if finding.file and finding.file not in files_changed:
                        files_changed.append(finding.file)
            files_json = json.dumps(files_changed, ensure_ascii=False)

            conn.execute(
                "INSERT INTO input_diffs (task_id, digest, redacted_summary, files_json, line_count) "
                "VALUES (?, ?, ?, ?, ?)",
                (report.task_id, digest, redacted_summary, files_json, 0)  # line_count 需要从 diff 解析
            )

    def _insert_sandbox_runs(self, conn: sqlite3.Connection, report: ReviewReport):
        """插入沙箱运行表（落库前脱敏 script/stdout/stderr）"""
        for run in report.sandbox_runs:
            # 验收5 命门：脱敏 script, stdout 和 stderr
            script_redacted, _ = redact_text(run.script)
            stdout_redacted, _ = redact_text(run.stdout_redacted)
            stderr_redacted, _ = redact_text(run.stderr_redacted)

            run_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO sandbox_runs "
                "(run_id, task_id, runtime, script, status, exit_code, "
                "stdout_redacted, stderr_redacted, truncated, error_type, duration_ms) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    report.task_id,
                    run.runtime,
                    script_redacted,  # 已脱敏
                    run.status,
                    run.exit_code,
                    stdout_redacted,  # 已脱敏
                    stderr_redacted,  # 已脱敏
                    1 if run.truncated else 0,
                    run.error_type,
                    run.duration_ms))

    def _insert_filter_decisions(self, conn: sqlite3.Connection, report: ReviewReport):
        """插入过滤决策表（落库前脱敏 reason/command）"""
        for decision in report.filter_decisions:
            # 验收5 命门：脱敏 reason 和 command_redacted
            reason_redacted, _ = redact_text(decision.reason)
            command_redacted, _ = redact_text(decision.command_redacted)

            decision_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO filter_decisions "
                "(decision_id, task_id, stage, decision, reason, command_redacted) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    decision_id,
                    report.task_id,
                    decision.stage,
                    decision.decision,
                    reason_redacted,  # 已脱敏
                    command_redacted  # 已脱敏
                ))

    def _insert_findings(self, conn: sqlite3.Connection, report: ReviewReport):
        """插入发现表（落库前脱敏 title/evidence/recommendation）"""
        # 合并所有 findings（warnings 和 needs_human_review 也是 Finding）
        all_findings = []
        if report.findings:
            all_findings.extend(report.findings)
        if report.warnings:
            all_findings.extend(report.warnings)
        if report.needs_human_review:
            all_findings.extend(report.needs_human_review)

        ignored = 0  # W8: 统计被 INSERT OR IGNORE 吞掉的 UNIQUE 冲突条数
        for finding in all_findings:
            # 验收5 命门：脱敏 title, evidence, recommendation
            title_redacted, _ = redact_text(finding.title)
            evidence_redacted, _ = redact_text(finding.evidence)
            recommendation_redacted, _ = redact_text(finding.recommendation)

            finding_id = str(uuid.uuid4())
            # 使用 INSERT OR IGNORE 处理 UNIQUE 约束（幂等）
            cursor = conn.execute(
                "INSERT OR IGNORE INTO findings "
                "(finding_id, task_id, bucket, severity, category, file, line, "
                "title, evidence, recommendation, confidence, source, rule_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    finding_id,
                    report.task_id,
                    finding.bucket.value,
                    finding.severity.value,
                    finding.category,
                    finding.file,
                    finding.line,
                    title_redacted,  # 已脱敏
                    evidence_redacted,  # 已脱敏
                    recommendation_redacted,  # 已脱敏
                    finding.confidence,
                    finding.source,
                    finding.rule_id))
            # W8: rowcount==0 表示该行因 UNIQUE 冲突被忽略
            if cursor.rowcount == 0:
                ignored += 1

        # W8: 若有被忽略的 finding，记录日志（幂等 save 下重复 save 可能丢新增）
        if ignored > 0:
            print(f"[Store] {ignored} 条 finding 因 UNIQUE 约束被 INSERT OR IGNORE 忽略")

    def _insert_monitoring_summary(self, conn: sqlite3.Connection, report: ReviewReport):
        """插入监控汇总表"""
        monitoring = report.monitoring

        conn.execute(
            "INSERT INTO monitoring_summaries "
            "(task_id, total_duration_ms, sandbox_duration_ms, tool_call_count, "
            "blocked_count, finding_count, severity_distribution, exception_distribution) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (report.task_id, monitoring.total_duration_ms, monitoring.sandbox_duration_ms, monitoring.tool_call_count,
             monitoring.blocked_count, monitoring.finding_count, json.dumps(
                 monitoring.severity_distribution), json.dumps(monitoring.exception_distribution)))

    def _insert_review_report(self, conn: sqlite3.Connection, report: ReviewReport):
        """插入审查报告表（多格式报告）

        验收5 命门：所有报告生成必须使用脱敏后的 input_summary
        """
        # 脱敏 input_summary（验收5 命门）
        input_summary_redacted, _ = redact_text(report.input_summary)

        # 构造报告的各格式版本
        report_dict = {
            "task_id": report.task_id,
            "status": report.status,
            "conclusion": report.conclusion,
            "repository": report.repository,
            "input_summary": input_summary_redacted,  # 使用脱敏后的摘要
            "findings_count": len(report.findings),
            "warnings_count": len(report.warnings),
            "needs_review_count": len(report.needs_human_review)
        }

        report_json = json.dumps(report_dict, ensure_ascii=False, indent=2)
        # 传递脱敏后的 input_summary 给报告生成方法
        report_md = self._generate_markdown_report(report, input_summary_redacted)
        report_sarif = self._generate_sarif_report(report, input_summary_redacted)

        conn.execute(
            "INSERT INTO review_reports "
            "(task_id, report_json, report_md, report_sarif) "
            "VALUES (?, ?, ?, ?)", (report.task_id, report_json, report_md, report_sarif))

    def _generate_markdown_report(self, report: ReviewReport, input_summary_redacted: str) -> str:
        """生成 Markdown 格式报告

        Args:
            report: 审查报告对象
            input_summary_redacted: 已脱敏的输入摘要（验收5 命门：必须使用脱敏版本）

        Returns:
            Markdown 格式的报告字符串
        """
        lines = [
            "# Code Review Report",
            "",
            f"**Task ID**: {report.task_id}",
            f"**Repository**: {report.repository}",
            f"**Conclusion**: {report.conclusion}",
            "",
            "## Summary",
            f"- Input: {input_summary_redacted}",  # 使用脱敏后的摘要（验收5 命门）
            f"- Findings: {len(report.findings)}",
            f"- Warnings: {len(report.warnings)}",
            f"- Needs Human Review: {len(report.needs_human_review)}",
            ""
        ]
        return "\n".join(lines)

    def _generate_sarif_report(self, report: ReviewReport, input_summary_redacted: str) -> str:
        """生成 SARIF 格式报告（简化版）

        Args:
            report: 审查报告对象
            input_summary_redacted: 已脱敏的输入摘要（验收5 命门：必须使用脱敏版本）

        Returns:
            SARIF 格式的 JSON 字符串
        """
        sarif = {
            "version":
            "2.1.0",
            "$schema":
            "https://json.schemastore.org/sarif-2.1.0.json",
            "runs": [{
                "tool": {
                    "driver": {
                        "name": "code-review-agent",
                        "version": "1.0.0"
                    }
                },
                "results": [],
                "invocations": [{
                    "exitCode":
                    0,
                    "toolExecutionNotifications": [{
                        "level": "note",
                        "message": {
                            "text": f"Input summary: {input_summary_redacted}"  # 使用脱敏后的摘要（验收5 命门）
                        }
                    }]
                }]
            }]
        }
        return json.dumps(sarif, ensure_ascii=False)

    def get_task_details(self, task_id: str) -> Optional[dict]:
        """获取任务完整详情（聚合七表，验收3 按task查）

        Args:
            task_id: 任务 ID

        Returns:
            包含七表数据的字典，如果任务不存在返回 None
        """
        conn = sqlite3.connect(self.path)
        try:
            # 1. 查询 review_tasks
            cursor = conn.cursor()
            cursor.execute(
                "SELECT task_id, status, conclusion, repository, scope, "
                "total_duration_ms, created_at, completed_at "
                "FROM review_tasks WHERE task_id=?", (task_id, ))
            task_row = cursor.fetchone()
            if not task_row:
                return None

            result = {
                "task_id": task_row[0],
                "status": task_row[1],
                "conclusion": task_row[2],
                "repository": task_row[3],
                "scope": task_row[4],
                "total_duration_ms": task_row[5],
                "created_at": task_row[6],
                "completed_at": task_row[7]
            }

            # 2. 查询 input_diffs
            cursor.execute("SELECT digest, redacted_summary, files_json, line_count FROM input_diffs WHERE task_id=?",
                           (task_id, ))
            diff_row = cursor.fetchone()
            if diff_row:
                result["input_diffs"] = {
                    "digest": diff_row[0],
                    "redacted_summary": diff_row[1],
                    "files_json": diff_row[2],
                    "line_count": diff_row[3]
                }

            # 3. 查询 sandbox_runs
            cursor.execute(
                "SELECT run_id, runtime, script, status, exit_code, stdout_redacted, "
                "stderr_redacted, truncated, error_type, duration_ms "
                "FROM sandbox_runs WHERE task_id=?", (task_id, ))
            result["sandbox_runs"] = [{
                "run_id": row[0],
                "runtime": row[1],
                "script": row[2],
                "status": row[3],
                "exit_code": row[4],
                "stdout_redacted": row[5],
                "stderr_redacted": row[6],
                "truncated": bool(row[7]),
                "error_type": row[8],
                "duration_ms": row[9]
            } for row in cursor.fetchall()]

            # 4. 查询 filter_decisions
            cursor.execute(
                "SELECT decision_id, stage, decision, reason, command_redacted "
                "FROM filter_decisions WHERE task_id=?", (task_id, ))
            result["filter_decisions"] = [{
                "decision_id": row[0],
                "stage": row[1],
                "decision": row[2],
                "reason": row[3],
                "command_redacted": row[4]
            } for row in cursor.fetchall()]

            # 5. 查询 findings（按 bucket 分离）
            cursor.execute(
                "SELECT finding_id, bucket, severity, category, file, line, "
                "title, evidence, recommendation, confidence, source, rule_id "
                "FROM findings WHERE task_id=?", (task_id, ))
            all_findings = []
            for row in cursor.fetchall():
                finding = {
                    "finding_id": row[0],
                    "bucket": row[1],
                    "severity": row[2],
                    "category": row[3],
                    "file": row[4],
                    "line": row[5],
                    "title": row[6],
                    "evidence": row[7],
                    "recommendation": row[8],
                    "confidence": row[9],
                    "source": row[10],
                    "rule_id": row[11]
                }
                all_findings.append(finding)

            # 按 bucket 分离
            result["findings"] = [f for f in all_findings if f["bucket"] == "findings"]
            result["warnings"] = [f for f in all_findings if f["bucket"] == "warnings"]
            result["needs_human_review"] = [f for f in all_findings if f["bucket"] == "needs_human_review"]

            # 6. 查询 monitoring_summaries
            cursor.execute(
                "SELECT total_duration_ms, sandbox_duration_ms, tool_call_count, "
                "blocked_count, finding_count, severity_distribution, exception_distribution "
                "FROM monitoring_summaries WHERE task_id=?", (task_id, ))
            monitoring_row = cursor.fetchone()
            if monitoring_row:
                result["monitoring"] = {
                    "total_duration_ms": monitoring_row[0],
                    "sandbox_duration_ms": monitoring_row[1],
                    "tool_call_count": monitoring_row[2],
                    "blocked_count": monitoring_row[3],
                    "finding_count": monitoring_row[4],
                    "severity_distribution": json.loads(monitoring_row[5]),
                    "exception_distribution": json.loads(monitoring_row[6])
                }

            # 7. 查询 review_reports
            cursor.execute("SELECT report_json, report_md, report_sarif "
                           "FROM review_reports WHERE task_id=?", (task_id, ))
            report_row = cursor.fetchone()
            if report_row:
                result["report_json"] = report_row[0]
                result["report_md"] = report_row[1]
                result["report_sarif"] = report_row[2]

            return result

        finally:
            conn.close()

    def mark_task_failed(self, task_id: str, error: str):
        """标记任务失败

        Args:
            task_id: 任务 ID
            error: 错误信息
        """
        conn = sqlite3.connect(self.path)
        conn.execute("UPDATE review_tasks SET status='failed', conclusion='failed' "
                     "WHERE task_id=?", (task_id, ))
        conn.commit()
        conn.close()
