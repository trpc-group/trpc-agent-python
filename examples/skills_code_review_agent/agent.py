import os
import sys
import json
import time
import uuid
import argparse
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Tuple

# Import db repository
from examples.skills_code_review_agent.db import ReviewDbRepository

class FilterGovernance:
    """
    Filter Governance policy checker for code review agent.
    """
    def __init__(self):
        self.forbidden_commands = ["rm -rf /", "curl", "wget", "nc", "bash -i", "sh -i"]
        self.forbidden_paths = ["/etc", "/var/run", "C:\\Windows"]
        self.whitelisted_domains = ["github.com", "pypi.org"]

    def check(self, command: str, inputs: List[str] = None) -> Tuple[bool, str]:
        # Rule 1: High-risk scripts/commands check
        import re
        for forbidden in self.forbidden_commands:
            if len(forbidden) <= 4:
                pattern = rf"\b{re.escape(forbidden)}\b"
                if re.search(pattern, command):
                    return False, f"Denied: Command contains forbidden high-risk execution pattern: '{forbidden}'"
            else:
                if forbidden in command:
                    return False, f"Denied: Command contains forbidden high-risk execution pattern: '{forbidden}'"
        
        # Rule 2: Forbidden paths and shell injection character checks
        all_checks = (inputs or []) + [command]
        shell_injection_pattern = r'[;&|`$]'
        for inp in all_checks:
            for forbidden_path in self.forbidden_paths:
                if forbidden_path in inp:
                    return False, f"Denied: Access to forbidden path '{forbidden_path}' is blocked"
            if re.search(shell_injection_pattern, inp):
                return False, f"Denied: Shell metacharacter injection detected in: '{inp}'"

        # Rule 3: Budget limit check (example: command length limit)
        if len(command) > 5000:
            return False, "Denied: Command exceeds budget safety length limit (5000 characters)"

        return True, "Allowed"

class CodeReviewAgent:
    """
    Main Code Review Agent orchestrator.
    """
    def __init__(self, db_url: str = "sqlite:///review_agent.db", runtime_mode: str = "local"):
        self.db = ReviewDbRepository(db_url)
        self.filter = FilterGovernance()
        self.runtime_mode = runtime_mode
        self.tool_call_count = 0
        self.block_count = 0

    def parse_diff(self, diff_content: str) -> Dict[str, Any]:
        """
        Parses diff content into files and lines.
        """
        self.tool_call_count += 1
        # Simple inline diff parser
        files = {}
        current_file = None
        current_line_num = 0

        for line in diff_content.splitlines():
            if line.startswith('diff --git'):
                current_file = None
            elif line.startswith('--- ') or line.startswith('+++ '):
                if line.startswith('+++ '):
                    parts = line.split(' ')
                    if len(parts) >= 2:
                        path = parts[1].replace('b/', '').strip().strip('"')
                        current_file = path
                        files[current_file] = []
            elif line.startswith('@@'):
                # @@ -1,4 +1,5 @@
                import re
                m = re.match(r'^@@\s+-\d+(?:,\d+)?\s+\+(\d+)(?:,\d+)?\s+@@', line)
                if m:
                    current_line_num = int(m.group(1))
            elif current_file is not None:
                if line.startswith('+'):
                    files[current_file].append({
                        'line': current_line_num,
                        'type': 'added',
                        'content': line[1:]
                    })
                    current_line_num += 1
                elif line.startswith('-'):
                    pass
                else:
                    files[current_file].append({
                        'line': current_line_num,
                        'type': 'context',
                        'content': line[1:] if len(line) > 0 else ''
                    })
                    current_line_num += 1
        return files

    def run_review(self, task_id: str, diff_file_path: str, fake_model: bool = True) -> Tuple[Dict[str, Any], str]:
        start_time = time.time()
        sandbox_time_ms = 0
        findings = []
        filter_logs = []
        sandbox_runs = []
        exception_types = {}

        # Load diff content
        if not os.path.exists(diff_file_path):
            self.db.update_task_status(task_id, "FAILED")
            raise FileNotFoundError(f"Diff file not found: {diff_file_path}")

        # Validate input path to prevent injection
        import re
        if re.search(r'[;&|`$]', diff_file_path):
            self.db.update_task_status(task_id, "INTERCEPTED")
            raise ValueError(f"Invalid characters in diff file path: {diff_file_path}")

        with open(diff_file_path, "r", encoding="utf-8", errors="ignore") as f:
            diff_content = f.read()

        # Parse diff
        parsed_diff = self.parse_diff(diff_content)
        
        # Save task start
        self.db.create_task(task_id, diff_content[:500])
        self.db.update_task_status(task_id, "IN_PROGRESS")

        # Set up sandbox files
        scripts_dir = Path(__file__).parent / "skills" / "code-review" / "scripts"
        
        # Prepare run command arguments (list style, shell=False to prevent command injection)
        parsed_diff_temp = Path(diff_file_path).parent / f"parsed_{task_id}.json"
        raw_findings_temp = Path(diff_file_path).parent / f"findings_{task_id}.json"
        
        parse_args = [sys.executable, str(scripts_dir / "parse_diff.py"), "--diff", str(diff_file_path), "--output", str(parsed_diff_temp)]
        parse_cmd_str = " ".join(parse_args)
        
        # Filter Governance check before execution - pass inputs correctly
        allowed, reason = self.filter.check(parse_cmd_str, inputs=[str(diff_file_path), str(parsed_diff_temp)])
        self.db.add_filter_log(task_id, "command_execution_filter", "ALLOW" if allowed else "DENY", reason)
        filter_logs.append({"rule_name": "command_execution_filter", "action": "ALLOW" if allowed else "DENY", "reason": reason})
        
        if not allowed:
            self.block_count += 1
            self.db.update_task_status(task_id, "INTERCEPTED")
            report_json, report_md = self.generate_reports(task_id, [], filter_logs, sandbox_runs, 0, start_time, status="INTERCEPTED")
            self.db.add_report(task_id, json.dumps(report_json), report_md, int((time.time() - start_time) * 1000))
            return report_json, report_md

        # Sandbox run execution
        sb_start = time.time()
        try:
            self.tool_call_count += 1
            # Run with shell=False using argument list
            res = subprocess.run(parse_args, shell=False, capture_output=True, text=True, timeout=15)
            sb_duration = int((time.time() - sb_start) * 1000)
            sandbox_time_ms += sb_duration
            
            stdout_limited = res.stdout[:5000]
            stderr_limited = res.stderr[:5000]
            status = "SUCCESS" if res.returncode == 0 else "FAILED"
            self.db.add_sandbox_run(task_id, parse_cmd_str, status, sb_duration, stdout_limited, stderr_limited)
            sandbox_runs.append({"command": parse_cmd_str, "status": status, "duration_ms": sb_duration, "stdout": stdout_limited, "stderr": stderr_limited})
            
            if res.returncode != 0:
                raise subprocess.SubprocessError(f"parse_diff script failed with exit code {res.returncode}: {stderr_limited}")
        except Exception as e:
            exception_name = type(e).__name__
            exception_types[exception_name] = exception_types.get(exception_name, 0) + 1
            self.db.update_task_status(task_id, "FAILED")
            report_json, report_md = self.generate_reports(task_id, [], filter_logs, sandbox_runs, sandbox_time_ms, start_time, status="FAILED", exception_types=exception_types)
            self.db.add_report(task_id, json.dumps(report_json), report_md, int((time.time() - start_time) * 1000))
            # Cleanup temp files if they exist
            if os.path.exists(parsed_diff_temp): os.remove(parsed_diff_temp)
            return report_json, report_md

        # Execute run_checks in sandbox
        check_args = [sys.executable, str(scripts_dir / "run_checks.py"), "--parsed-diff", str(parsed_diff_temp), "--output", str(raw_findings_temp)]
        check_cmd_str = " ".join(check_args)
            
        allowed, reason = self.filter.check(check_cmd_str, inputs=[str(parsed_diff_temp), str(raw_findings_temp)])
        self.db.add_filter_log(task_id, "command_execution_filter", "ALLOW" if allowed else "DENY", reason)
        filter_logs.append({"rule_name": "command_execution_filter", "action": "ALLOW" if allowed else "DENY", "reason": reason})

        if not allowed:
            self.block_count += 1
            self.db.update_task_status(task_id, "INTERCEPTED")
            if os.path.exists(parsed_diff_temp): os.remove(parsed_diff_temp)
            report_json, report_md = self.generate_reports(task_id, [], filter_logs, sandbox_runs, sandbox_time_ms, start_time, status="INTERCEPTED")
            self.db.add_report(task_id, json.dumps(report_json), report_md, int((time.time() - start_time) * 1000))
            return report_json, report_md

        sb_start = time.time()
        try:
            self.tool_call_count += 1
            res = subprocess.run(check_args, shell=False, capture_output=True, text=True, timeout=15)
            sb_duration = int((time.time() - sb_start) * 1000)
            sandbox_time_ms += sb_duration
            
            stdout_limited = res.stdout[:5000]
            stderr_limited = res.stderr[:5000]
            status = "SUCCESS" if res.returncode == 0 else "FAILED"
            self.db.add_sandbox_run(task_id, check_cmd_str, status, sb_duration, stdout_limited, stderr_limited)
            sandbox_runs.append({"command": check_cmd_str, "status": status, "duration_ms": sb_duration, "stdout": stdout_limited, "stderr": stderr_limited})
            
            if res.returncode == 0 and os.path.exists(raw_findings_temp):
                with open(raw_findings_temp, "r", encoding="utf-8") as f:
                    raw_findings = json.load(f)
            else:
                raise subprocess.SubprocessError(f"run_checks script failed with exit code {res.returncode}: {stderr_limited}")
        except Exception as e:
            exception_name = type(e).__name__
            exception_types[exception_name] = exception_types.get(exception_name, 0) + 1
            self.db.update_task_status(task_id, "FAILED")
            report_json, report_md = self.generate_reports(task_id, [], filter_logs, sandbox_runs, sandbox_time_ms, start_time, status="FAILED", exception_types=exception_types)
            self.db.add_report(task_id, json.dumps(report_json), report_md, int((time.time() - start_time) * 1000))
            if os.path.exists(parsed_diff_temp): os.remove(parsed_diff_temp)
            if os.path.exists(raw_findings_temp): os.remove(raw_findings_temp)
            return report_json, report_md

        # Deduplication and noise reduction
        seen = set()
        deduped_findings = []
        for f in raw_findings:
            key = (f["file"], f["line"], f["category"])
            if key not in seen:
                seen.add(key)
                deduped_findings.append(f)

        # Store findings in db
        self.db.add_findings(task_id, deduped_findings)
        
        # Cleanup temp files
        if os.path.exists(parsed_diff_temp): os.remove(parsed_diff_temp)
        if os.path.exists(raw_findings_temp): os.remove(raw_findings_temp)

        # Update task status
        self.db.update_task_status(task_id, "COMPLETED")

        # Generate final reports
        report_json, report_md = self.generate_reports(
            task_id, 
            deduped_findings, 
            filter_logs, 
            sandbox_runs, 
            sandbox_time_ms, 
            start_time,
            exception_types
        )
        
        self.db.add_report(task_id, json.dumps(report_json), report_md, int((time.time() - start_time) * 1000))
        return report_json, report_md

    def generate_reports(self, 
                         task_id: str, 
                         findings: List[Dict[str, Any]], 
                         filter_logs: List[Dict[str, Any]], 
                         sandbox_runs: List[Dict[str, Any]], 
                         sandbox_time_ms: int, 
                         start_time: float,
                         exception_types: Dict[str, int] = None,
                         status: str = None) -> Tuple[Dict[str, Any], str]:
        total_time_ms = int((time.time() - start_time) * 1000)
        
        # Deduplication noise reduction: split high vs low confidence
        high_conf_findings = [f for f in findings if f.get("confidence") == "high"]
        low_conf_findings = [f for f in findings if f.get("confidence") != "high"]
        
        severity_dist = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for f in findings:
            sev = f.get("severity", "medium").lower()
            if sev in severity_dist:
                severity_dist[sev] += 1
            else:
                severity_dist[sev] = severity_dist.get(sev, 0) + 1

        # Build json report
        if status is None:
            status = "COMPLETED" if not any(l["action"] == "DENY" for l in filter_logs) else "INTERCEPTED"
            
        report_json = {
            "task_id": task_id,
            "status": status,
            "findings": high_conf_findings,
            "needs_human_review": low_conf_findings,
            "filter_intercept_summary": [l for l in filter_logs if l["action"] == "DENY"],
            "sandbox_runs": sandbox_runs,
            "metrics": {
                "total_duration_ms": total_time_ms,
                "sandbox_duration_ms": sandbox_time_ms,
                "tool_calls": self.tool_call_count,
                "blocks": self.block_count,
                "findings_count": len(findings),
                "severity_distribution": severity_dist,
                "exception_types": exception_types or {}
            }
        }

        # Build md report
        md = []
        md.append(f"# Code Review Report (Task: {task_id})")
        md.append("")
        md.append(f"**Status**: {report_json['status']}")
        md.append(f"**Total Duration**: {total_time_ms} ms")
        md.append(f"**Sandbox execution duration**: {sandbox_time_ms} ms")
        md.append("")
        
        md.append("## Findings Summary")
        md.append(f"- Critical: {severity_dist['critical']}")
        md.append(f"- High: {severity_dist['high']}")
        md.append(f"- Medium: {severity_dist['medium']}")
        md.append(f"- Low: {severity_dist['low']}")
        md.append("")

        if high_conf_findings:
            md.append("## High Confidence Findings")
            for idx, f in enumerate(high_conf_findings, 1):
                md.append(f"### {idx}. [{f['severity'].upper()}] {f['title']}")
                md.append(f"- **Category**: {f['category']}")
                md.append(f"- **File**: {f['file']}:{f['line']}")
                md.append(f"- **Evidence**: `{f['evidence']}`")
                md.append(f"- **Recommendation**: {f['recommendation']}")
                md.append("")
        else:
            md.append("No high-confidence findings found.")
            md.append("")

        if low_conf_findings:
            md.append("## Manual Review / Low Confidence Warnings")
            for idx, f in enumerate(low_conf_findings, 1):
                md.append(f"### {idx}. [{f['severity'].upper()}] {f['title']}")
                md.append(f"- **Category**: {f['category']}")
                md.append(f"- **File**: {f['file']}:{f['line']}")
                md.append(f"- **Evidence**: `{f['evidence']}`")
                md.append(f"- **Recommendation**: {f['recommendation']}")
                md.append("")

        if report_json["filter_intercept_summary"]:
            md.append("## Filter Interceptions")
            for l in report_json["filter_intercept_summary"]:
                md.append(f"- **Rule**: {l['rule_name']} - **Reason**: {l['reason']}")
            md.append("")

        md.append("## Sandbox Execution Details")
        for r in sandbox_runs:
            md.append(f"- **Command**: `{r['command']}` ({r['status']} in {r['duration_ms']} ms)")
            if r['stderr']:
                md.append(f"  - **Stderr**: `{r['stderr']}`")
        md.append("")

        return report_json, "\n".join(md)

def print_console_summary(report_json: dict):
    try:
        from rich.console import Console
        from rich.table import Table
        from rich.panel import Panel
        from rich import box
        import platform
        import sys
        if platform.system() == "Windows" and sys.stdout.encoding.lower() != "utf-8":
            raise ImportError("Fallback to plain text table on non-UTF8 Windows terminal")
        
        console = Console()
        console.print(Panel.fit(
            f"[bold green]Code Review Completed successfully![/bold green]\n"
            f"Task ID: [cyan]{report_json['task_id']}[/cyan] | Status: [yellow]{report_json['status']}[/yellow]",
            title="tRPC-Agent Code Review"
        ))
        
        metrics = report_json["metrics"]
        console.print(f"[bold]Execution Summary:[/bold] Total Time: [bold cyan]{metrics['total_duration_ms']}ms[/bold cyan] | "
                      f"Sandbox Execution Time: [bold cyan]{metrics['sandbox_duration_ms']}ms[/bold cyan]")
        
        # Table of findings with ASCII box to support all command page encodings
        table = Table(show_header=True, header_style="bold magenta", box=box.ASCII)
        table.add_column("Severity", width=12)
        table.add_column("Category", width=25)
        table.add_column("File:Line", width=30)
        table.add_column("Title", width=40)
        
        findings = report_json["findings"] + report_json["needs_human_review"]
        if findings:
            for f in findings:
                sev = f["severity"].upper()
                sev_style = "bold red" if sev in ("CRITICAL", "HIGH") else "bold yellow" if sev == "MEDIUM" else "bold green"
                table.add_row(
                    f"[{sev_style}]{sev}[/{sev_style}]",
                    f["category"],
                    f"{f['file']}:{f['line']}",
                    f["title"]
                )
            console.print(table)
        else:
            console.print("[bold green]✔ No findings detected in the code changes![/bold green]")
            
        if report_json["filter_intercept_summary"]:
            console.print("\n[bold red]Interceptions Detected:[/bold red]")
            for intercept in report_json["filter_intercept_summary"]:
                console.print(f"  - [red]{intercept['rule_name']}[/red]: {intercept['reason']}")
    except ImportError:
        # Fallback to simple console print if rich is not installed
        print("="*80)
        print(f"Code Review Task {report_json['task_id']} Completed! Status: {report_json['status']}")
        print(f"Total Time: {report_json['metrics']['total_duration_ms']}ms | Sandbox Time: {report_json['metrics']['sandbox_duration_ms']}ms")
        print("="*80)
        findings = report_json["findings"] + report_json["needs_human_review"]
        if findings:
            print(f"{'Severity':<10} | {'Category':<20} | {'File:Line':<25} | {'Title'}")
            print("-"*80)
            for f in findings:
                print(f"{f['severity'].upper():<10} | {f['category']:<20} | {f'{f['file']}:{f['line']}':<25} | {f['title']}")
        else:
            print("No findings detected.")
        print("="*80)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Code Review Agent CLI")
    parser.add_argument("--diff-file", help="Path to unified diff file")
    parser.add_argument("--repo-path", help="Path to local repository")
    parser.add_argument("--fake-model", action="store_true", default=True, help="Use dry-run/fake model mode")
    parser.add_argument("--runtime", default="local", choices=["local", "container"], help="Sandbox runtime mode")
    parser.add_argument("--db-url", default="sqlite:///review_agent.db", help="Database connection URL")
    args = parser.parse_args()

    if args.diff_file:
        agent = CodeReviewAgent(db_url=args.db_url, runtime_mode=args.runtime)
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        print(f"Starting review task {task_id} on {args.diff_file}...")
        report_json, report_md = agent.run_review(task_id, args.diff_file, fake_model=args.fake_model)
        
        # Write outputs
        with open("review_report.json", "w", encoding="utf-8") as f:
            json.dump(report_json, f, indent=2)
        with open("review_report.md", "w", encoding="utf-8") as f:
            f.write(report_md)
            
        print("Review completed. Reports written to review_report.json and review_report.md")
        print_console_summary(report_json)
    else:
        parser.print_help()
