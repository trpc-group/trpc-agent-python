"""End-to-end orchestration for the skills code review agent example."""

from __future__ import annotations

import time
import uuid
from pathlib import Path

from .diff_parser import load_diff
from .filter_policy import ReviewFilterPolicy
from .filter_policy import SandboxRequest
from .models import DiffInput
from .models import FilterDecision
from .models import Finding
from .models import ReviewReport
from .models import SandboxRun
from .models import utc_now
from .redaction import redact_text
from .reporting import render_markdown
from .reporting import write_reports
from .rule_engine import build_finding
from .rule_engine import RuleEngine
from .rule_engine import FINDING_SCHEMA_VERSION
from .rule_engine import FINDING_CONFIDENCE_THRESHOLD
from .rule_engine import WARNING_CONFIDENCE_THRESHOLD
from .sandbox import FakeSandboxRunner
from .sandbox import ENV_WHITELIST
from .sandbox import LocalSandboxRunner
from .sandbox import SandboxRunner
from .sandbox import WorkspaceSandboxRunner
from .skill_loader import load_code_review_skill
from .storage import SQLiteReviewStore
from .storage import ReviewStore
from .telemetry import build_monitoring_summary

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[1] / "sample_outputs"
DEFAULT_DB_PATH = DEFAULT_OUTPUT_DIR / "review_tasks.sqlite"
SKILL_DIR = Path(__file__).resolve().parents[1] / "skills" / "code-review"
FILTER_POLICY_PATH = SKILL_DIR / "filter_policy.json"


async def run_review(
    *,
    diff_file: Path | None = None,
    patch_file: Path | None = None,
    repo_path: Path | None = None,
    fixture: str | None = None,
    file_list: Path | None = None,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    db_path: Path = DEFAULT_DB_PATH,
    db_url: str | None = None,
    sandbox: str = "fake",
    dry_run: bool = True,
    container_image: str = "python:3-slim",
    docker_path: str | None = None,
    docker_base_url: str | None = None,
    cube_template: str | None = None,
    cube_api_url: str | None = None,
    cube_api_key: str | None = None,
    cube_sandbox_id: str | None = None,
    timeout_sec: float = 5.0,
    max_output_bytes: int = 12000,
    filter_timeout_budget_sec: float = 30.0,
    filter_max_output_bytes: int = 20000,
    network_policy: str = "deny",
    test_command: str | None = None,
    custom_rule_script: str | None = None,
    include_network_scanners: bool = False,
    max_diff_bytes: int = 2_000_000,
    store: ReviewStore | None = None,
    sandbox_runner: SandboxRunner | None = None,
) -> ReviewReport:
    start = time.monotonic()
    stage_durations_ms: dict[str, int] = {}

    def mark_stage(name: str, stage_start: float) -> None:
        stage_durations_ms[name] = int((time.monotonic() - stage_start) * 1000)

    parse_start = time.monotonic()
    diff = load_diff(
        diff_file=diff_file,
        patch_file=patch_file,
        repo_path=repo_path,
        fixture=fixture,
        file_list=file_list,
        max_diff_bytes=max_diff_bytes,
    )
    diff, input_redactions = _redact_diff(diff)
    skill_audit = load_code_review_skill(SKILL_DIR)
    mark_stage("parse", parse_start)
    task_id = f"cr_{uuid.uuid4().hex[:12]}"
    created_at = utc_now()
    review_store = store or (ReviewStore.from_url(db_url) if db_url else SQLiteReviewStore(db_path))
    storage_start = time.monotonic()
    review_store.create_task(task_id,
                             source=diff.source,
                             created_at=created_at,
                             diff_summary=diff.summary,
                             diff_text=diff.diff_text)
    mark_stage("storage_create_task", storage_start)

    try:
        return await _run_review_after_task_created(
            start=start,
            stage_durations_ms=stage_durations_ms,
            task_id=task_id,
            created_at=created_at,
            diff=diff,
            input_redactions=input_redactions,
            skill_audit=skill_audit,
            review_store=review_store,
            output_dir=output_dir,
            sandbox=sandbox,
            dry_run=dry_run,
            container_image=container_image,
            docker_path=docker_path,
            docker_base_url=docker_base_url,
            cube_template=cube_template,
            cube_api_url=cube_api_url,
            cube_api_key=cube_api_key,
            cube_sandbox_id=cube_sandbox_id,
            timeout_sec=timeout_sec,
            max_output_bytes=max_output_bytes,
            filter_timeout_budget_sec=filter_timeout_budget_sec,
            filter_max_output_bytes=filter_max_output_bytes,
            network_policy=network_policy,
            test_command=test_command,
            custom_rule_script=custom_rule_script,
            include_network_scanners=include_network_scanners,
            sandbox_runner=sandbox_runner,
        )
    except Exception as exc:
        redacted = redact_text(str(exc))
        review_store.fail_task(
            task_id,
            completed_at=utc_now(),
            exception_type=exc.__class__.__name__,
            message=redacted.text,
        )
        raise


async def _run_review_after_task_created(
    *,
    start: float,
    stage_durations_ms: dict[str, int],
    task_id: str,
    created_at: str,
    diff: DiffInput,
    input_redactions: int,
    skill_audit: dict,
    review_store: ReviewStore,
    output_dir: Path,
    sandbox: str,
    dry_run: bool,
    container_image: str,
    docker_path: str | None,
    docker_base_url: str | None,
    cube_template: str | None,
    cube_api_url: str | None,
    cube_api_key: str | None,
    cube_sandbox_id: str | None,
    timeout_sec: float,
    max_output_bytes: int,
    filter_timeout_budget_sec: float,
    filter_max_output_bytes: int,
    network_policy: str,
    test_command: str | None,
    custom_rule_script: str | None,
    include_network_scanners: bool,
    sandbox_runner: SandboxRunner | None,
) -> ReviewReport:

    def mark_stage(name: str, stage_start: float) -> None:
        stage_durations_ms[name] = int((time.monotonic() - stage_start) * 1000)

    skill_audit["sdk_skill_runtime"] = {
        "executed": False,
        "reason": (
            "SDK skill_load/skill_run smoke is available through --skill-smoke; normal reviews do not execute "
            "local workspace runtime before Filter approval."
        ),
    }

    filter_start = time.monotonic()
    sandbox_requests, request_build_decisions, request_build_redactions = _build_sandbox_requests_for_review(
        timeout_sec=timeout_sec,
        max_output_bytes=max_output_bytes,
        test_command=test_command,
        custom_rule_script=custom_rule_script,
        include_network_scanners=include_network_scanners,
    )
    if "sandbox_failure" in diff.source:
        sandbox_requests.append(
            SandboxRequest(
                name="forced_failure_probe",
                command="python scripts/static_review.py --force-failure",
                script_path="scripts/static_review.py",
                timeout_sec=timeout_sec,
                max_output_bytes=max_output_bytes,
                env={},
            ))
    filter_policy = ReviewFilterPolicy.load(
        FILTER_POLICY_PATH,
        network_policy=network_policy,
        timeout_budget_sec=filter_timeout_budget_sec,
        max_output_bytes=filter_max_output_bytes,
    )
    allowed_requests, filter_decisions = filter_policy.evaluate(diff, sandbox_requests)
    filter_decisions = request_build_decisions + filter_decisions
    mark_stage("filter", filter_start)
    storage_start = time.monotonic()
    review_store.save_filter_decisions(task_id, filter_decisions)
    mark_stage("storage_filter_decisions", storage_start)

    runner = sandbox_runner or await _make_runner(
        sandbox=sandbox,
        dry_run=dry_run,
        container_image=container_image,
        docker_path=docker_path,
        docker_base_url=docker_base_url,
        cube_template=cube_template,
        cube_api_url=cube_api_url,
        cube_api_key=cube_api_key,
        cube_sandbox_id=cube_sandbox_id,
    )
    sandbox_runs = []
    sandbox_start = time.monotonic()
    for request in allowed_requests:
        run = await _run_sandbox_request_safely(runner, request, diff)
        sandbox_runs.append(run)
    mark_stage("sandbox", sandbox_start)
    storage_start = time.monotonic()
    review_store.save_sandbox_runs(task_id, sandbox_runs)
    mark_stage("storage_sandbox_runs", storage_start)

    rules_start = time.monotonic()
    rule_engine = RuleEngine()
    findings, warnings, needs_human_review, rule_redactions, deduped_finding_count = rule_engine.review(diff)
    scanner_findings, scanner_warnings, scanner_needs_review, scanner_redactions = _findings_from_scanner_runs(
        sandbox_runs, rule_engine)
    findings.extend(scanner_findings)
    warnings.extend(scanner_warnings)
    needs_human_review.extend(scanner_needs_review)
    findings, warnings, needs_human_review, merged_deduped_count = _dedupe_finding_buckets(
        findings,
        warnings,
        needs_human_review,
    )
    deduped_finding_count += merged_deduped_count
    sandbox_redactions = sum(run.redaction_count for run in sandbox_runs)
    mark_stage("rules", rules_start)
    storage_start = time.monotonic()
    review_store.save_findings(task_id, "finding", findings)
    review_store.save_findings(task_id, "warning", warnings)
    review_store.save_findings(task_id, "needs_human_review", needs_human_review)
    mark_stage("storage_findings", storage_start)

    total_duration_ms = int((time.monotonic() - start) * 1000)
    monitoring = build_monitoring_summary(
        total_duration_ms=total_duration_ms,
        stage_durations_ms=stage_durations_ms,
        sandbox_runs=sandbox_runs,
        findings=findings,
        warnings=warnings,
        needs_human_review=needs_human_review,
        filter_decisions=filter_decisions,
        filter_decision_count=len(filter_decisions),
        redaction_count=(
            input_redactions
            + request_build_redactions
            + filter_policy.last_redaction_count
            + rule_redactions
            + scanner_redactions
            + sandbox_redactions
        ),
        deduped_finding_count=deduped_finding_count,
        ignored_finding_count=rule_engine.ignored_count,
    )
    status = "completed"
    conclusion = _build_conclusion(findings, warnings, needs_human_review, sandbox_runs, filter_decisions)
    report = ReviewReport(
        task_id=task_id,
        status=status,
        created_at=created_at,
        finding_schema_version=FINDING_SCHEMA_VERSION,
        confidence_thresholds={
            "finding": FINDING_CONFIDENCE_THRESHOLD,
            "warning": WARNING_CONFIDENCE_THRESHOLD,
        },
        sandbox_policy={
            "runtime": runner.runtime_name,
            "timeout_sec": timeout_sec,
            "max_output_bytes": max_output_bytes,
            "filter_timeout_budget_sec": filter_timeout_budget_sec,
            "filter_max_output_bytes": filter_max_output_bytes,
            "env_whitelist": sorted(ENV_WHITELIST),
            "network_policy": network_policy,
            "network_enforcement": _network_enforcement_summary(runner.runtime_name),
        },
        filter_policy=filter_policy.audit(),
        input=diff.to_dict(),
        findings=findings,
        warnings=warnings,
        needs_human_review=needs_human_review,
        filter_decisions=filter_decisions,
        sandbox_runs=sandbox_runs,
        monitoring=monitoring,
        conclusion=conclusion,
        skill_audit={
            **skill_audit, "rule_config": rule_engine.rule_config.audit()
        },
    )
    report_start = time.monotonic()
    output_dir.mkdir(parents=True, exist_ok=True)
    report.output_files.update({
        "json": str(output_dir / "review_report.json"),
        "markdown": str(output_dir / "review_report.md"),
    })
    _ = render_markdown(report)
    mark_stage("report", report_start)
    report.monitoring.stage_durations_ms = stage_durations_ms
    report.monitoring.total_duration_ms = int((time.monotonic() - start) * 1000)
    _, _, markdown = write_reports(report, output_dir)
    review_store.complete_task(report, markdown, completed_at=utc_now())
    review_store.save_monitoring(task_id, report.monitoring)
    return report


async def _run_sandbox_request_safely(
    runner: SandboxRunner,
    request: SandboxRequest,
    diff: DiffInput,
) -> SandboxRun:
    start = time.monotonic()
    try:
        return await runner.run(request, diff, skill_dir=SKILL_DIR)
    except Exception as exc:  # pylint: disable=broad-except
        redacted = redact_text(str(exc))
        return SandboxRun(
            name=request.name,
            runtime=runner.runtime_name,
            command=request.command,
            status="failed",
            exit_code=None,
            duration_ms=int((time.monotonic() - start) * 1000),
            stdout="",
            stderr=redacted.text[:request.max_output_bytes],
            exception_type=exc.__class__.__name__,
            output_truncated=len(redacted.text) > request.max_output_bytes,
            redaction_count=redacted.count,
        )


def query_task(db_path: Path, task_id: str) -> dict:
    return SQLiteReviewStore(db_path).get_task_bundle(task_id)


def _redact_diff(diff: DiffInput) -> tuple[DiffInput, int]:
    redacted = redact_text(diff.diff_text)
    if redacted.count == 0:
        return diff, 0
    return load_diff_from_text(redacted.text, source=diff.source), redacted.count


def load_diff_from_text(text: str, *, source: str) -> DiffInput:
    from .diff_parser import parse_unified_diff

    return parse_unified_diff(text, source=source)


def _build_sandbox_requests(
    *,
    timeout_sec: float,
    max_output_bytes: int,
    test_command: str | None = None,
    custom_rule_script: str | None = None,
    include_network_scanners: bool = False,
) -> list[SandboxRequest]:
    requests = [
        SandboxRequest(
            name="diff_summary",
            command="python scripts/diff_summary.py",
            script_path="scripts/diff_summary.py",
            timeout_sec=timeout_sec,
            max_output_bytes=max_output_bytes,
            env={},
        ),
        SandboxRequest(
            name="static_review",
            command="python scripts/static_review.py",
            script_path="scripts/static_review.py",
            timeout_sec=timeout_sec,
            max_output_bytes=max_output_bytes,
            env={},
        ),
        SandboxRequest(
            name="test_probe",
            command="python scripts/test_probe.py",
            script_path="scripts/test_probe.py",
            timeout_sec=timeout_sec,
            max_output_bytes=max_output_bytes,
            env={},
        ),
        SandboxRequest(
            name="scanner_probe",
            command="python scripts/scanner_probe.py",
            script_path="scripts/scanner_probe.py",
            timeout_sec=timeout_sec,
            max_output_bytes=max_output_bytes,
            env={},
            write_allowlist=("work/", ),
        ),
    ]
    if include_network_scanners:
        requests.append(
            SandboxRequest(
                name="semgrep_network_probe",
                command="python scripts/scanner_probe.py --semgrep-auto",
                script_path="scripts/scanner_probe.py",
                timeout_sec=timeout_sec,
                max_output_bytes=max_output_bytes,
                env={},
                network_required=True,
                network_domains=("semgrep.dev", "registry.semgrep.dev"),
                write_allowlist=("work/", ),
            ))
    if test_command:
        requests.append(
            SandboxRequest(
                name="unit_tests",
                command="python scripts/unit_test_probe.py",
                script_path="scripts/unit_test_probe.py",
                timeout_sec=timeout_sec,
                max_output_bytes=max_output_bytes,
                env={
                    "CR_TEST_COMMAND": test_command,
                    "CR_ALLOW_TEST_COMMAND": "1",
                    "CR_TEST_TIMEOUT": str(timeout_sec)
                },
                read_allowlist=("work/", "scripts/", "repo/"),
            ))
    if custom_rule_script:
        script_path = _validate_custom_rule_script(custom_rule_script)
        requests.append(
            SandboxRequest(
                name=f"custom_rule:{Path(script_path).stem}",
                command=f"python {script_path}",
                script_path=script_path,
                timeout_sec=timeout_sec,
                max_output_bytes=max_output_bytes,
                env={},
            ))
    return requests


def _build_sandbox_requests_for_review(
    *,
    timeout_sec: float,
    max_output_bytes: int,
    test_command: str | None = None,
    custom_rule_script: str | None = None,
    include_network_scanners: bool = False,
) -> tuple[list[SandboxRequest], list[FilterDecision], int]:
    """Build requests for the pipeline without letting invalid script input crash review."""
    try:
        return (
            _build_sandbox_requests(
                timeout_sec=timeout_sec,
                max_output_bytes=max_output_bytes,
                test_command=test_command,
                custom_rule_script=custom_rule_script,
                include_network_scanners=include_network_scanners,
            ),
            [],
            0,
        )
    except (ValueError, FileNotFoundError) as exc:
        requests = _build_sandbox_requests(
            timeout_sec=timeout_sec,
            max_output_bytes=max_output_bytes,
            test_command=test_command,
            custom_rule_script=None,
            include_network_scanners=include_network_scanners,
        )
        redacted_path = redact_text(custom_rule_script or "")
        redacted_reason = redact_text(str(exc))
        return requests, [
            FilterDecision(
                decision="deny",
                reason=redacted_reason.text,
                command=f"python {redacted_path.text}" if redacted_path.text else "",
                path=redacted_path.text,
                policy="custom-rule-script-validation",
                severity="high",
            )
        ], redacted_path.count + redacted_reason.count


def _validate_custom_rule_script(script_path: str) -> str:
    normalized = Path(script_path).as_posix().lstrip("/")
    if ".." in Path(normalized).parts:
        raise ValueError("custom rule script must not contain '..'")
    if not normalized.startswith("scripts/"):
        raise ValueError("custom rule script must live under the code-review Skill scripts/ directory")
    if not normalized.endswith(".py"):
        raise ValueError("custom rule script must be a Python script")
    if not (SKILL_DIR / normalized).exists():
        raise FileNotFoundError(f"custom rule script not found: {normalized}")
    return normalized


async def _make_runner(
    *,
    sandbox: str,
    dry_run: bool,
    container_image: str = "python:3-slim",
    docker_path: str | None = None,
    docker_base_url: str | None = None,
    cube_template: str | None = None,
    cube_api_url: str | None = None,
    cube_api_key: str | None = None,
    cube_sandbox_id: str | None = None,
) -> SandboxRunner:
    if dry_run or sandbox == "fake":
        return FakeSandboxRunner()
    if sandbox == "local":
        return LocalSandboxRunner()
    if sandbox == "container":
        from .runtime_factory import create_container_sandbox_runner

        return create_container_sandbox_runner(
            image=container_image,
            docker_path=docker_path,
            base_url=docker_base_url,
        )
    if sandbox == "cube":
        from .runtime_factory import create_cube_sandbox_runner_from_config

        return await create_cube_sandbox_runner_from_config(
            template=cube_template,
            api_url=cube_api_url,
            api_key=cube_api_key,
            sandbox_id=cube_sandbox_id,
        )
    raise ValueError(f"Unknown sandbox runtime: {sandbox}")


def build_workspace_sandbox_runner(runtime, runtime_name: str) -> WorkspaceSandboxRunner:
    """Build a production sandbox adapter from Container or Cube workspace runtime."""
    if runtime_name not in {"container", "cube", "local"}:
        raise ValueError("runtime_name must be one of: container, cube, local")
    return WorkspaceSandboxRunner(runtime, runtime_name)


def _build_conclusion(findings, warnings, needs_human_review, sandbox_runs, filter_decisions) -> str:
    critical_or_high = [f for f in findings if f.severity in {"critical", "high"}]
    failed_runs = [r for r in sandbox_runs if r.status in {"failed", "timeout"}]
    blocked_requests = [d for d in filter_decisions if d.decision in {"deny", "needs_human_review"}]
    if critical_or_high:
        return "Block merge until high-severity findings are fixed."
    if failed_runs or needs_human_review or blocked_requests:
        return "Needs human review before merge because automation found uncertain, failed, or blocked checks."
    if warnings:
        return "Mergeable after reviewer accepts warnings or adds missing tests."
    return "No blocking issues detected by the offline review pipeline."


def _network_enforcement_summary(runtime_name: str) -> str:
    if runtime_name == "container":
        return (
            "container host_config sets network_mode=none; network-backed scanners require a separately approved "
            "runtime image/policy."
        )
    if runtime_name == "cube":
        return (
            "Filter gates requested network domains; per-run egress enforcement must be provided by the Cube/E2B "
            "workspace policy."
        )
    if runtime_name == "fake":
        return "fake runtime simulates scanner behavior without network access."
    if runtime_name == "local":
        return "local runtime is development fallback only and does not enforce network isolation."
    return "custom runtime must enforce network isolation according to the active Filter policy."


def _dedupe_finding_buckets(
    findings: list[Finding],
    warnings: list[Finding],
    needs_human_review: list[Finding],
) -> tuple[list[Finding], list[Finding], list[Finding], int]:
    """Deduplicate merged rule and scanner results across all report buckets."""
    original_count = len(findings) + len(warnings) + len(needs_human_review)
    findings = _dedupe_finding_bucket(findings)
    finding_keys = {finding.key() for finding in findings}
    warnings = _dedupe_finding_bucket([finding for finding in warnings if finding.key() not in finding_keys])
    warning_keys = {finding.key() for finding in warnings}
    needs_human_review = _dedupe_finding_bucket([
        finding for finding in needs_human_review
        if finding.key() not in finding_keys and finding.key() not in warning_keys
    ])
    final_count = len(findings) + len(warnings) + len(needs_human_review)
    return findings, warnings, needs_human_review, original_count - final_count


def _dedupe_finding_bucket(findings: list[Finding]) -> list[Finding]:
    """Deduplicate one bucket after rule and scanner results are merged."""
    seen: set[tuple[str, int, str]] = set()
    out: list[Finding] = []
    severity_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
    for finding in sorted(
            findings,
            key=lambda item: (
                -severity_rank.get(item.severity, 0),
                -item.confidence,
                item.file,
                item.line,
                item.category,
                item.source,
            ),
    ):
        key = finding.key()
        if key in seen:
            continue
        seen.add(key)
        out.append(finding)
    return out


def _findings_from_scanner_runs(sandbox_runs,
                                rule_engine: RuleEngine) -> tuple[list[Finding], list[Finding], list[Finding], int]:
    findings: list[Finding] = []
    warnings: list[Finding] = []
    needs_human_review: list[Finding] = []
    redactions = 0
    for run in sandbox_runs:
        if not run.stdout.strip().startswith("{"):
            continue
        try:
            import json

            payload = json.loads(run.stdout)
        except Exception:
            continue
        if "scanner_runs" not in payload:
            continue
        for scanner_run in payload.get("scanner_runs", []):
            for item in scanner_run.get("findings", []):
                evidence = str(item.get("evidence", ""))
                redacted = redact_text(evidence)
                redactions += redacted.count
                finding = build_finding(
                    severity=str(item.get("severity", "medium")),
                    category="secret_leak" if str(item.get("severity")) == "critical"
                    and "secret" in str(item.get("rule_id", "")) else "security",
                    file=_normalize_scanner_file(str(item.get("file", ""))),
                    line=int(item.get("line") or 1),
                    title=str(item.get("title", "External scanner finding")),
                    evidence=redacted.text,
                    recommendation=str(item.get("recommendation", "Review and fix the external scanner finding.")),
                    confidence=float(item.get("confidence", 0.75)),
                    source=f"scanner:{scanner_run.get('name', item.get('scanner', 'unknown'))}",
                    rule_id=str(item.get("rule_id", "scanner.issue")),
                )
                configured = rule_engine.rule_config.apply(finding)
                if configured is None:
                    rule_engine.ignored_count += 1
                    continue
                finding = configured
                if finding.confidence >= 0.8:
                    findings.append(finding)
                elif finding.confidence >= 0.55:
                    warnings.append(finding)
                else:
                    needs_human_review.append(finding)
    return findings, warnings, needs_human_review, redactions


def _normalize_scanner_file(path: str) -> str:
    normalized = path.replace("\\", "/")
    marker = "/cr-scan-"
    if marker in normalized:
        parts = normalized.split(marker, 1)[1].split("/", 1)
        if len(parts) == 2:
            return parts[1]
    return normalized
