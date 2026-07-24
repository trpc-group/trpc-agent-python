# agent/pipeline.py —— 串联全链路（可信层 + LLM 增强层）
"""
Task 12: 编排管线 + CLI
核心函数: run_review(diff_text, repo, sandbox="fake", dry_run=True, llm=False) -> ReviewReport

管线流程：
1. parse_diff(diff_text) → files
2. review_rules(files) + ast_analyzer.analyze(files) → findings
3. (若 llm=True) llm_layer.enhance(findings, files, dry_run) → findings（可选，dry_run 时走预录制）
4. dedup_and_route(findings) → (findings, warnings, needs_review)
5. Filter 前置：对每个沙箱脚本（约定名如 ["static_review","diff_summary"]）调 CommandPolicy.evaluate；
   只有 allow 才 build_runtime(sandbox).run(script)；deny/needs_human_review 不进沙箱（记录 FilterDecision）
6. telemetry.build_monitoring(...) 聚合监控
7. conclusion 派生：有 critical/high finding → changes_requested；有 warnings 或 filter block → needs_human_review；
   沙箱失败 → completed_with_warnings；都无 → approve
8. ReviewStore().save(report)（落库，内含脱敏）
9. report.write_reports(report, "outputs/")
"""
import os
import shutil
import time
import uuid

from agent.diff_parser import parse_diff
from agent.rule_engine import review_rules
from agent.ast_analyzer import analyze
from agent.dedup import dedup_and_route
from agent.models import ReviewReport, Severity
from agent.telemetry import build_monitoring
from agent.report import write_reports
from agent.redaction import redact_text, redact_finding
from storage.store import ReviewStore
from filters.policy import CommandPolicy, load_policy
from sandbox.factory import build_runtime

# 沙箱脚本约定名（Task 13 会创建真实脚本文件）
SKILL_SCRIPTS = ["static_review", "diff_summary"]

# 沙箱脚本源目录（skills/code-review/scripts/）
SCRIPTS_SRC_DIR = os.path.join(os.path.dirname(__file__), "..", "skills", "code-review", "scripts")


def _conclusion(findings, warnings, needs_review, decisions, sandbox_runs) -> str:
    """派生 conclusion：根据 findings/warnings/decisions/sandbox_runs 决定最终结论

    规则（优先级从高到低）：
    1. 有 critical/high finding → changes_requested
    2. 有 warnings 或 filter block → needs_human_review
    3. 沙箱失败 → completed_with_warnings
    4. 都无 → approve
    """
    # 1. 有 critical/high finding → changes_requested (最高优先级)
    # Warning 修复: 覆盖 findings + warnings 两桶，避免高危安全问题(如 SEC008 SSRF
    #   confidence=0.75 进 warnings 桶)只得出 needs_human_review 而非 changes_requested
    for bucket in (findings, warnings):
        for finding in bucket:
            if finding.severity in [Severity.CRITICAL, Severity.HIGH]:
                return "changes_requested"

    # 2. 有 warnings 或 filter block → needs_human_review
    if warnings:
        return "needs_human_review"

    has_filter_block = any(d.decision in ["deny", "needs_human_review"] for d in decisions)
    if has_filter_block:
        return "needs_human_review"

    # 3. 沙箱失败 → completed_with_warnings
    has_failed_run = any(run.status in ["failed", "timeout"] for run in sandbox_runs)
    if has_failed_run:
        return "completed_with_warnings"

    # 4. 都无 → approve
    return "approve"


def _summary(diff_text: str) -> str:
    """生成输入摘要（脱敏后）"""
    if not diff_text:
        return ""
    # 简单截断前 200 字符作为摘要
    summary = diff_text[:200] + ("..." if len(diff_text) > 200 else "")
    # 脱敏
    redacted_summary, _ = redact_text(summary)
    return redacted_summary


def run_review(diff_text: str,
               repo: str,
               sandbox: str = "fake",
               dry_run: bool = True,
               llm: bool = False) -> ReviewReport:
    """执行代码审查管线：串联全链路

    Args:
        diff_text: unified diff 格式的代码变更文本
        repo: 仓库 URL 或路径
        sandbox: 沙箱后端类型（fake/local/container/cube），默认 fake
        dry_run: 是否为 dry_run 模式（LLM 层使用预录制数据），默认 True
        llm: 是否启用 LLM 增强，默认 False

    Returns:
        ReviewReport: 完整的审查报告
    """
    t0 = time.time()

    # 0. 修复隐患4: 先 start_task 创建 task_id（确保 save 时 review_tasks 记录存在）
    # Critical 3 修复: start_task 失败时置 store=None，避免 save 对孤儿 task_id 落库
    store = None
    try:
        store = ReviewStore()
        scope = f"diff-{len(diff_text)}chars" if diff_text else "empty"
        task_id = store.start_task(repo=repo, scope=scope)
    except Exception as e:
        # start_task 失败时使用生成的 UUID（降级处理），并标记不落库
        print(f"[Pipeline] start_task 失败: {str(e)}")
        task_id = str(uuid.uuid4())
        store = None

    # 1. 解析 diff
    files = parse_diff(diff_text)

    # 2. 规则引擎 + AST 分析
    findings = review_rules(files) + analyze(files)

    # 脱敏 findings（验收5 命门：所有可能含密文的字段都要脱敏）
    findings = [redact_finding(f) for f in findings]

    # 3. LLM 增强（可选）
    if llm:
        from agent.llm_layer import enhance
        findings = enhance(findings, files, dry_run=dry_run)
        # LLM 增强后也要脱敏
        findings = [redact_finding(f) for f in findings]

    # 4. 去重 + 三桶路由
    findings, warnings, needs_review = dedup_and_route(findings)

    # 5. Filter 前置决策 + 沙箱执行
    runs = []
    decisions = []

    # 加载策略
    policy = CommandPolicy(load_policy())

    # 对每个沙箱脚本进行 Filter 决策
    for script in SKILL_SCRIPTS:
        # 构造命令字符串用于评估
        command = f"python {script}"

        # 调用 CommandPolicy.evaluate
        decision = policy.evaluate(command, {"call_index": len(runs)})
        decisions.append(decision)

        # 只有 allow 才进沙箱执行
        if decision.decision == "allow":
            try:
                # 构建运行时
                runtime = build_runtime(sandbox)

                # 执行脚本（使用临时工作目录）
                import tempfile
                with tempfile.TemporaryDirectory() as ws:
                    # 修复隐患3: 把脚本文件写入 workspace（真沙箱需要脚本存在）
                    script_src = os.path.join(SCRIPTS_SRC_DIR, f"{script}.py")
                    script_dst = os.path.join(ws, f"{script}.py")
                    if os.path.exists(script_src):
                        shutil.copy(script_src, script_dst)
                    else:
                        # 脚本不存在时记录失败但不中断（防御性编程）
                        from agent.models import SandboxRun
                        failed_run = SandboxRun(
                            runtime=sandbox,
                            script=script,
                            status="failed",
                            exit_code=1,
                            stdout_redacted="",
                            stderr_redacted=f"脚本不存在: {script_src}",
                            truncated=False,
                            error_type="FileNotFoundError",
                            duration_ms=0
                        )
                        runs.append(failed_run)
                        continue

                    run = runtime.run(
                        script=f"{script}.py",
                        workspace=ws,
                        inputs={"diff_text": diff_text}
                    )
                    runs.append(run)
            except Exception as e:
                # 沙箱执行失败，记录失败但不中断
                from agent.models import SandboxRun
                failed_run = SandboxRun(runtime=sandbox,
                                        script=script,
                                        status="failed",
                                        exit_code=1,
                                        stdout_redacted="",
                                        stderr_redacted=str(e),
                                        truncated=False,
                                        error_type=type(e).__name__,
                                        duration_ms=0)
                runs.append(failed_run)

    # 6. 构建监控摘要
    # S1 修复: sandbox_duration 累加各沙箱 run 实际耗时（而非等于 total_duration）
    t_sandbox = sum(run.duration_ms for run in runs)

    # 合并所有 findings 用于监控
    all_findings = list(findings) + list(warnings) + list(needs_review)

    monitoring = build_monitoring(
        total_duration_ms=int((time.time() - t0) * 1000),
        sandbox_duration_ms=t_sandbox,
        tool_call_count=len(runs),
        blocked_count=sum(1 for d in decisions if d.decision != "allow"),
        findings=all_findings,
        exceptions=[]
    )

    # 7. 派生 conclusion（使用已生成的 task_id）
    conclusion = _conclusion(findings, warnings, needs_review, decisions, runs)

    # 9. 构造 ReviewReport
    report = ReviewReport(task_id=task_id,
                          status="completed",
                          conclusion=conclusion,
                          findings=list(findings),
                          warnings=list(warnings),
                          needs_human_review=list(needs_review),
                          filter_decisions=decisions,
                          sandbox_runs=runs,
                          monitoring=monitoring,
                          repository=repo,
                          input_summary=_summary(diff_text))

    # 9. 落库（内含脱敏，使用已存在的 task_id）
    # Critical 3 修复: store=None（start_task 失败）时跳过落库，避免外键孤儿
    if store is not None:
        try:
            store.save(report)
        except Exception as e:
            # 落库失败不应中断报告生成
            print(f"[Pipeline] 落库失败: {str(e)}")
    else:
        print(f"[Pipeline] 跳过落库（task 未注册，task_id={task_id}）")

    # 10. 写报告文件
    try:
        write_reports(report, "outputs/")
    except Exception as e:
        # 报告生成失败不应中断主流程
        print(f"[Pipeline] 报告生成失败: {str(e)}")

    return report
