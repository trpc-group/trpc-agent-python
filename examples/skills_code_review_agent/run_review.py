#!/usr/bin/env python3
# Tencent is pleased to support the open source community by making trpc-agent-python available.
# Copyright (C) 2025 Tencent. All rights reserved.
# trpc-agent-python is licensed under the Apache License Version 2.0.
"""
Automated Code Review Agent — CLI entry point.

Two modes:
  (default)  Synchronous pipeline: diff → rules → dedup → redact → report
  --agent    Agent-driven: LlmAgent + SkillToolSet + Runner + FakeModel/LLM

Usage:
    python run_review.py --diff-file path/to/changes.diff
    python run_review.py --diff-file path/to/changes.diff --agent
    python run_review.py --diff-file path/to/changes.diff --agent --model gpt-4o-mini
"""
import argparse
import asyncio
import json
import sys
import os
import time
from datetime import datetime, timezone
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description='Automated Code Review Agent'
    )
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument('--diff-file', type=str, help='Path to unified diff file')
    input_group.add_argument('--repo-path', type=str, help='Path to git repository (uses git diff)')
    input_group.add_argument('--files', type=str, nargs='*', help='File path list (generates synthetic diff)')
    parser.add_argument('--dry-run', action='store_true', dest='dry_run', default=True,
                        help='Dry-run: rules only, no sandbox execution (default)')
    parser.add_argument('--no-dry-run', action='store_false', dest='dry_run',
                        help='Enable sandbox execution')
    parser.add_argument('--agent', action='store_true', default=False,
                        help='Use Agent-driven pipeline (LlmAgent + SkillToolSet + Runner)')
    parser.add_argument('--model', type=str, default=None,
                        help='LLM model name (e.g., gpt-4o-mini). Default: fake-model for Agent mode')
    parser.add_argument('--output', type=str, default='./output',
                        help='Output directory for reports (default: ./output)')
    parser.add_argument('--sandbox', type=str, default='container',
                        choices=['local', 'container', 'cube'],
                        help='Sandbox runtime type (default: container; local for dev)')
    parser.add_argument('--db', type=str, default=None,
                        help='SQLite database path')
    return parser.parse_args()


def generate_task_id() -> str:
    import uuid
    return uuid.uuid4().hex[:12]


# ============================================================
# Synchronous Pipeline (existing, kept for --dry-run mode)
# ============================================================

def run_sync_pipeline(args, task_id: str, diff_text: str):
    """Run the synchronous (non-Agent) code review pipeline."""
    from agent.diff_parser import parse_diff
    from agent.rule_engine import run_rules
    from agent.dedup import dedup_findings
    from agent.filter import check_dangerous
    from agent.redaction import redact_findings, redact_text
    from sandbox.runner import SandboxRunner
    from storage.schema import ReviewStore
    from report.json_report import generate_json_report
    from report.markdown_report import generate_markdown_report

    start_time = time.time()
    output_dir = Path(args.output) / task_id
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path = args.db or str(output_dir / 'review.db')
    sandbox_duration_ms = 0
    intercept_count = 0
    exception_dist: dict[str, int] = {}

    store = ReviewStore(db_path)
    input_source = (f"diff:{args.diff_file}" if args.diff_file
                    else f"repo:{args.repo_path}" if args.repo_path
                    else f"files:{len(args.files)}" if args.files
                    else "unknown")
    store.create_task(task_id, input_type='diff',
                      diff_summary=f'{len(diff_text)} bytes from {input_source}')

    parsed = parse_diff(diff_text)
    file_count = len(parsed.get('files', []))
    added_lines = parsed.get('total_added_lines', 0)
    print(f"[review] Parsed {file_count} files, {added_lines} lines added")

    findings = run_rules(parsed)
    print(f"[review] Found {len(findings)} findings from rule engine")

    findings, warnings = dedup_findings(findings)
    print(f"[review] After dedup: {len(findings)} findings, {len(warnings)} warnings")

    blocked, needs_review, allowed = check_dangerous(findings)
    intercept_count = 0
    review_count = 0
    for f in findings:
        action = f.get('filter_action', 'allow')
        store.save_filter_decision(task_id, action=action,
                                   rule=f.get('rule_id', ''),
                                   reason=f.get('filter_reason', ''))
        if action == 'deny':
            intercept_count += 1
        elif action in ('ask', 'needs_human_review'):
            review_count += 1
    if blocked:
        print(f"[filter] Denied {len(blocked)} destructive items (blocked from sandbox)")
        findings = [f for f in findings if f.get('filter_action') != 'deny']
    if needs_review:
        # needs_review items stay in findings but are flagged for human
        print(f"[filter] Flagged {len(needs_review)} items for human review "
              f"(ask={sum(1 for f in needs_review if f.get('filter_action') == 'ask')} "
              f"needs_review={sum(1 for f in needs_review if f.get('filter_action') == 'needs_human_review')})")
    print(f"[filter] {len(allowed)} allowed, {len(blocked)} denied, "
          f"{len(needs_review)} flagged for human review")

    # Sandbox execution
    active_scripts_dir = Path(__file__).parent / 'skills' / 'code-review' / 'scripts'
    if not args.dry_run and active_scripts_dir.exists():
        runner = SandboxRunner(sandbox_type=args.sandbox)
        parsed_json = None
        diff_tmp = output_dir / '_input.diff'
        diff_tmp.write_text(diff_text, encoding='utf-8')

        parse_diff_script = active_scripts_dir / 'parse_diff.py'
        if parse_diff_script.exists():
            print(f"[sandbox] Running: parse_diff.py <diff>")
            result = runner.run_script(str(parse_diff_script), stdin_input=diff_text)
            result['stdout'] = redact_text(result.get('stdout', ''))
            result['stderr'] = redact_text(result.get('stderr', ''))
            if result.get('exception_type'):
                exc_type = result['exception_type']
                exception_dist[exc_type] = exception_dist.get(exc_type, 0) + 1
            store.save_sandbox_run(task_id, result)
            sandbox_duration_ms += result.get('duration_ms', 0)
            if result.get('exit_code') == 0 and result.get('stdout', '').strip():
                parsed_json = result['stdout']
                print(f"[sandbox] parse_diff.py OK ({result.get('duration_ms', 0)}ms)")
            else:
                print(f"[sandbox] parse_diff.py exit={result['exit_code']}")

        static_check_script = active_scripts_dir / 'static_check.py'
        if static_check_script.exists():
            print(f"[sandbox] Running: static_check.py")
            result = runner.run_script(str(static_check_script), stdin_input=parsed_json)
            result['stdout'] = redact_text(result.get('stdout', ''))
            result['stderr'] = redact_text(result.get('stderr', ''))
            if result.get('exception_type'):
                exc_type = result['exception_type']
                exception_dist[exc_type] = exception_dist.get(exc_type, 0) + 1
            store.save_sandbox_run(task_id, result)
            sandbox_duration_ms += result.get('duration_ms', 0)
            exit_msg = "OK" if result.get('exit_code') == 0 else f"exit={result['exit_code']}"
            print(f"[sandbox] static_check.py {exit_msg} ({result.get('duration_ms', 0)}ms)")

        diff_tmp.unlink(missing_ok=True)
    elif not args.dry_run:
        store.save_sandbox_run(task_id, {'script': '__dry_run_skipped__', 'exit_code': 0,
                                         'stdout': 'Sandbox skipped (no scripts)', 'stderr': '',
                                         'duration_ms': 0, 'timed_out': False})

    findings = redact_findings(findings)
    warnings = redact_findings(warnings)
    store.save_findings(task_id, findings)
    for w in warnings:
        store.save_finding(task_id, w, is_warning=True)
    print(f"[store] Saved {len(findings)} findings, {len(warnings)} warnings")

    total_duration_ms = int((time.time() - start_time) * 1000)
    severity_dist = {
        'critical': sum(1 for f in findings if f['severity'] == 'critical'),
        'high': sum(1 for f in findings if f['severity'] == 'high'),
        'medium': sum(1 for f in findings if f['severity'] == 'medium'),
        'low': sum(1 for f in findings if f['severity'] == 'low'),
    }
    store.save_monitoring(task_id, {
        'total_duration_ms': total_duration_ms,
        'sandbox_duration_ms': sandbox_duration_ms,
        'tool_call_count': len(findings) + len(warnings),
        'intercept_count': intercept_count,
        'finding_count': len(findings),
        'severity_distribution': severity_dist,
        'exception_distribution': exception_dist,
    })

    monitoring = {
        'task_id': task_id, 'total_duration_ms': total_duration_ms,
        'sandbox_duration_ms': sandbox_duration_ms, 'file_count': file_count,
        'total_added_lines': added_lines, 'finding_count': len(findings),
        'warning_count': len(warnings), 'intercept_count': intercept_count,
        'review_count': review_count, 'severity_distribution': severity_dist,
    }
    details = store.get_task_details(task_id)
    report = {
        'task_id': task_id, 'findings': findings, 'warnings': warnings,
        'monitoring': monitoring,
        'filter_decisions': details.get('filter_decisions', []),
        'sandbox_runs': details.get('sandbox_runs', []),
    }

    json_path = output_dir / 'review_report.json'
    json_content = json.dumps(report, indent=2, ensure_ascii=False)
    json_path.write_text(json_content, encoding='utf-8')
    store.save_report(task_id, 'json', json_content)

    md_path = output_dir / 'review_report.md'
    md_content = generate_markdown_report(report)
    md_path.write_text(md_content, encoding='utf-8')
    store.save_report(task_id, 'markdown', md_content)

    store.complete_task(task_id, total_duration_ms, file_count, added_lines)
    store.close()

    print(f"[review] Reports: {json_path}, {md_path}, {db_path}")
    sev = severity_dist
    print(f"[summary] {total_duration_ms}ms | {file_count} files, {added_lines} lines | "
          f"Findings: {len(findings)} (C:{sev['critical']} H:{sev['high']} M:{sev['medium']} L:{sev['low']}) | "
          f"Warnings: {len(warnings)} | Intercepts: {intercept_count} | Sandbox: {sandbox_duration_ms}ms")


# ============================================================
# Agent Pipeline (new — uses LlmAgent + SkillToolSet + Runner)
# ============================================================

async def run_agent_pipeline_async(args, task_id: str, diff_text: str):
    """Run code review using the Agent-driven pipeline."""
    from trpc_agent_sdk.agents import LlmAgent
    from trpc_agent_sdk.runners import Runner
    from trpc_agent_sdk.sessions import InMemorySessionService
    from trpc_agent_sdk.types import Content, Part
    from trpc_agent_sdk.skills import SkillToolSet, create_default_skill_repository
    from trpc_agent_sdk.code_executors import create_local_workspace_runtime

    from agent.fake_model import FakeModel, build_code_review_steps
    from agent.diff_parser import parse_diff
    from agent.rule_engine import run_rules
    from agent.dedup import dedup_findings
    from agent.filter import check_dangerous
    from agent.redaction import redact_findings, redact_text
    from storage.schema import ReviewStore
    from report.json_report import generate_json_report
    from report.markdown_report import generate_markdown_report

    start_time = time.time()
    output_dir = Path(args.output) / task_id
    output_dir.mkdir(parents=True, exist_ok=True)
    db_path = args.db or str(output_dir / 'review.db')

    store = ReviewStore(db_path)
    input_source = (f"diff:{args.diff_file}" if args.diff_file
                    else f"repo:{args.repo_path}" if args.repo_path
                    else f"files:{len(args.files)}" if args.files
                    else "unknown")
    store.create_task(task_id, input_type='agent',
                      diff_summary=f'{len(diff_text)} bytes from {input_source}')

    # Write diff to temp file for skill_run to access
    diff_tmp = output_dir / '_input.diff'
    diff_tmp.write_text(diff_text, encoding='utf-8')

    # Create model
    if args.model:
        from trpc_agent_sdk.models import OpenAIModel
        model = OpenAIModel(
            model_name=args.model,
            api_key=os.environ.get('TRPC_AGENT_API_KEY', ''),
            base_url=os.environ.get('TRPC_AGENT_BASE_URL', ''),
        )
        print(f"[review] Using model: {args.model}")
    else:
        model = FakeModel(model_name="fake-model")
        model.set_steps(build_code_review_steps(str(diff_tmp)))
        print(f"[review] Using fake model with {len(model._steps)} pre-recorded steps")

    # Create Skill repository and toolset (use CopySkillStager on Windows)
    from trpc_agent_sdk.skills.tools import CopySkillStager
    skills_dir = str(Path(__file__).parent / 'skills')
    workspace_runtime = create_local_workspace_runtime()
    repo = create_default_skill_repository(skills_dir, workspace_runtime=workspace_runtime)
    skill_toolset = SkillToolSet(repository=repo, skill_stager=CopySkillStager())

    # Create agent with code_review_safety filter
    import agent.filter_agent  # noqa: F401 — triggers @register_agent_filter
    agent = LlmAgent(
        name="code_review_agent",
        description="Analyzes git diffs for security, resource, error, and testing issues",
        model=model,
        instruction=(
            "You are a code review agent. When you receive a diff, load the "
            "'code-review' skill, review its documentation, then run "
            "'parse_diff.py' and 'static_check.py' in order. Report findings."
        ),
        tools=[skill_toolset],
        skill_repository=repo,
        filters_name=["code_review_safety"],
    )

    session_service = InMemorySessionService()
    runner = Runner(
        app_name="code_review_agent",
        agent=agent,
        session_service=session_service,
    )

    user_id = "reviewer"
    session_id = task_id
    message = f"Please review the code diff at {diff_tmp}"
    print(f"[agent] Starting Agent pipeline...")

    findings_text = []
    sandbox_start = 0.0
    sandbox_duration_ms = 0
    exception_dist: dict[str, int] = {}
    try:
        async for event in runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=Content(parts=[Part.from_text(text=message)]),
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.function_call and not event.partial:
                        fn_name = part.function_call.name
                        if fn_name in ("skill_run", "skill_exec"):
                            sandbox_start = time.time()
                        print(f"[agent] → {fn_name}({part.function_call.args})")
                    elif part.function_response and not event.partial:
                        if sandbox_start > 0:
                            sandbox_duration_ms += int((time.time() - sandbox_start) * 1000)
                            sandbox_start = 0
                        resp_data = part.function_response.response
                        resp = str(resp_data)[:100]
                        if isinstance(resp_data, dict):
                            err = resp_data.get('error', '') or resp_data.get('status', '')
                            if err and err != 'success':
                                exc_key = f"Agent:{err}" if isinstance(err, str) else "Agent:error"
                                exception_dist[exc_key] = exception_dist.get(exc_key, 0) + 1
                        store.save_sandbox_run(task_id, {
                            'script': f"agent_{event.author or 'tool'}",
                            'exit_code': 0,
                            'stdout': redact_text(resp),
                            'stderr': '',
                            'duration_ms': 0,
                            'timed_out': False,
                        })
                        print(f"[agent] ← {resp}")
                    elif part.text and not event.partial:
                        print(f"[agent]   {part.text[:200]}")
                        findings_text.append(part.text)
    except Exception as e:
        exc_key = type(e).__name__
        exception_dist[exc_key] = exception_dist.get(exc_key, 0) + 1
        print(f"[agent] Agent execution error: {e}")

    diff_tmp.unlink(missing_ok=True)

    # Post-processing: Agent handles orchestration (skill_load/skill_run).
    # The deterministic pipeline below produces structured findings from the
    # raw diff text, independent of the Agent's output. This ensures finding
    # quality does not depend on LLM behavior.
    parsed = parse_diff(diff_text)
    findings = run_rules(parsed)
    findings, warnings = dedup_findings(findings)

    blocked, needs_review_agent, allowed = check_dangerous(findings)
    intercept_count = 0
    review_count = 0
    for f in findings:
        action = f.get('filter_action', 'allow')
        store.save_filter_decision(task_id, action=action,
                                   rule=f.get('rule_id', ''),
                                   reason=f.get('filter_reason', ''))
        if action == 'deny':
            intercept_count += 1
        elif action in ('ask', 'needs_human_review'):
            review_count += 1
    if blocked:
        findings = [f for f in findings if f.get('filter_action') != 'deny']

    findings = redact_findings(findings)
    warnings = redact_findings(warnings)
    store.save_findings(task_id, findings)
    for w in warnings:
        store.save_finding(task_id, w, is_warning=True)

    total_duration_ms = int((time.time() - start_time) * 1000)
    file_count = len(parsed.get('files', []))
    added_lines = parsed.get('total_added_lines', 0)
    severity_dist = {
        'critical': sum(1 for f in findings if f['severity'] == 'critical'),
        'high': sum(1 for f in findings if f['severity'] == 'high'),
        'medium': sum(1 for f in findings if f['severity'] == 'medium'),
        'low': sum(1 for f in findings if f['severity'] == 'low'),
    }

    store.save_monitoring(task_id, {
        'total_duration_ms': total_duration_ms, 'sandbox_duration_ms': sandbox_duration_ms,
        'tool_call_count': len(findings) + len(warnings),
        'intercept_count': intercept_count, 'finding_count': len(findings),
        'severity_distribution': severity_dist,
        'exception_distribution': exception_dist,
    })

    monitoring = {
        'task_id': task_id, 'total_duration_ms': total_duration_ms,
        'sandbox_duration_ms': sandbox_duration_ms,
        'file_count': file_count, 'total_added_lines': added_lines,
        'finding_count': len(findings), 'warning_count': len(warnings),
        'intercept_count': intercept_count, 'review_count': review_count,
        'severity_distribution': severity_dist,
    }
    details = store.get_task_details(task_id)
    report_data = {
        'task_id': task_id, 'findings': findings, 'warnings': warnings,
        'monitoring': monitoring, 'agent_output': '\n'.join(findings_text),
        'filter_decisions': details.get('filter_decisions', []),
        'sandbox_runs': details.get('sandbox_runs', []),
    }

    json_path = output_dir / 'review_report.json'
    json_content = json.dumps(report_data, indent=2, ensure_ascii=False)
    json_path.write_text(json_content, encoding='utf-8')
    store.save_report(task_id, 'json', json_content)

    md_path = output_dir / 'review_report.md'
    md_content = generate_markdown_report(report_data)
    md_path.write_text(md_content, encoding='utf-8')
    store.save_report(task_id, 'markdown', md_content)

    store.complete_task(task_id, total_duration_ms, file_count, added_lines)
    store.close()

    print(f"[review] Reports: {json_path}, {md_path}, {db_path}")
    sev = severity_dist
    print(f"[summary] {total_duration_ms}ms | {file_count} files, {added_lines} lines | "
           f"Findings: {len(findings)} (C:{sev['critical']} H:{sev['high']} M:{sev['medium']} L:{sev['low']}) | "
           f"Warnings: {len(warnings)} | Intercepts: {intercept_count}")
    if sandbox_duration_ms:
        print(f"[summary] Sandbox time: {sandbox_duration_ms}ms")
    print(f"[agent] Agent mode complete")


# ============================================================
# Main
# ============================================================

def main():
    args = parse_args()

    # Load diff
    if args.diff_file:
        diff_path = Path(args.diff_file)
        if not diff_path.exists():
            print(f"[error] Diff file not found: {args.diff_file}")
            sys.exit(1)
        diff_text = diff_path.read_text(encoding='utf-8')
    elif args.repo_path:
        repo_path = Path(args.repo_path)
        if not repo_path.exists():
            print(f"[error] Repo path not found: {args.repo_path}")
            sys.exit(1)
        import subprocess as sp
        result = sp.run(['git', 'diff'], cwd=str(repo_path),
                        capture_output=True, text=True, encoding='utf-8',
                        errors='replace')
        if result.returncode != 0:
            print(f"[error] git diff failed: {result.stderr.strip()}")
            sys.exit(1)
        diff_text = result.stdout
        if not diff_text.strip():
            print("[error] No changes detected in working tree. Make some changes first.")
            sys.exit(1)
        print(f"[review] Generated diff from repo: {repo_path} ({len(diff_text)} bytes)")
    elif args.files:
        diffs: list[str] = []
        for fp in args.files:
            p = Path(fp)
            if not p.exists():
                print(f"[warning] File not found, skipping: {fp}")
                continue
            content = p.read_text(encoding='utf-8', errors='replace')
            diffs.append(f"diff --git a/{p.name} b/{p.name}\n"
                         f"--- a/{p.name}\n"
                         f"+++ b/{p.name}\n"
                         f"@@ -0,0 +1,{len(content.splitlines())} @@\n")
            for line in content.splitlines():
                diffs.append(f"+{line}\n")
        if not diffs:
            print("[error] No valid files specified")
            sys.exit(1)
        diff_text = ''.join(diffs)
        print(f"[review] Generated diff from {len(args.files)} file(s) ({len(diff_text)} bytes)")
    else:
        print("[error] No input specified")
        sys.exit(1)

    task_id = generate_task_id()
    print(f"[review] Task ID: {task_id}")
    print(f"[review] Mode: {'Agent' if args.agent else 'sync'}"
          f" {'+ sandbox' if not args.dry_run else '(dry-run)'}"
          f"{' ' + args.model if args.model else ' (fake-model)' if args.agent else ''}")

    if args.agent:
        asyncio.run(run_agent_pipeline_async(args, task_id, diff_text))
    else:
        run_sync_pipeline(args, task_id, diff_text)


if __name__ == '__main__':
    main()
