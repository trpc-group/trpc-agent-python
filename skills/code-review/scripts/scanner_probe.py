"""Run optional third-party scanners in the sandbox and normalize their output."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from pathlib import PurePosixPath

SCANNERS = {
    "bandit": ["bandit", "-r", "{root}", "-f", "json"],
    "ruff": ["ruff", "check", "{root}", "--output-format=json"],
    "detect-secrets": ["detect-secrets", "scan", "{root}", "--all-files"],
}

NETWORK_SCANNERS = {
    "semgrep": ["semgrep", "--config", "auto", "--json", "{root}"],
}


def main() -> int:
    diff_text = sys.stdin.read()
    if len(sys.argv) > 1:
        diff_text = Path(sys.argv[1]).read_text(encoding="utf-8")
    work_dir = Path("work")
    work_dir.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cr-scan-", dir=work_dir) as tmp:
        root = Path(tmp)
        _materialize_added_files(diff_text, root)
        runs = []
        scanners = dict(SCANNERS)
        if "--semgrep-auto" in sys.argv:
            scanners.update(NETWORK_SCANNERS)
        for name, command_template in scanners.items():
            runs.append(_run_scanner(name, command_template, root))
    print(json.dumps({"scanner_runs": runs}, sort_keys=True))
    return 0


def _run_scanner(name: str, command_template: list[str], root: Path) -> dict[str, object]:
    executable = command_template[0]
    if shutil.which(executable) is None:
        return {"name": name, "status": "skipped", "reason": f"{executable} not installed", "findings": []}
    command = [part.format(root=str(root)) for part in command_template]
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=20, check=False)
    except subprocess.TimeoutExpired:
        return {"name": name, "status": "timeout", "reason": "scanner timed out", "findings": []}
    findings = _normalize_findings(name, result.stdout)
    status = "passed" if result.returncode == 0 else "issues_found" if findings else "failed"
    return {
        "name": name,
        "status": status,
        "exit_code": result.returncode,
        "findings": findings[:50],
        "stderr": result.stderr[:2000],
    }


def _materialize_added_files(diff_text: str, root: Path) -> None:
    current: Path | None = None
    lines: list[str] = []
    root_resolved = root.resolve()
    for raw in diff_text.splitlines():
        if raw.startswith("+++ b/"):
            if current is not None:
                _write_file(current, lines)
            current = _safe_added_file_path(root_resolved, raw.removeprefix("+++ b/").strip())
            lines = []
            continue
        if current is None:
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            lines.append(raw[1:])
        elif raw.startswith(" ") and lines:
            lines.append(raw[1:])
    if current is not None:
        _write_file(current, lines)


def _write_file(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _safe_added_file_path(root: Path, raw_path: str) -> Path | None:
    path = raw_path.split("\t", 1)[0].strip()
    if not path or "\x00" in path or path == "/dev/null":
        return None
    rel = PurePosixPath(path)
    if rel.is_absolute() or ".." in rel.parts:
        return None
    target = (root / rel.as_posix()).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target


def _normalize_findings(name: str, stdout: str) -> list[dict[str, object]]:
    if not stdout.strip():
        return []
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return []
    if name == "bandit":
        return [{
            "scanner":
            name,
            "rule_id":
            f"scanner.bandit.{item.get('test_id', 'issue')}",
            "severity":
            _severity(item.get("issue_severity", "medium")),
            "file":
            item.get("filename", ""),
            "line":
            int(item.get("line_number") or 1),
            "title":
            item.get("test_name", "Bandit finding"),
            "evidence":
            item.get("code", ""),
            "recommendation":
            item.get("issue_text", "Review Bandit finding and apply the recommended secure coding fix."),
            "confidence":
            0.88,
        } for item in data.get("results", [])]
    if name == "ruff":
        return [{
            "scanner": name,
            "rule_id": f"scanner.ruff.{item.get('code', 'issue')}",
            "severity": "medium",
            "file": item.get("filename", ""),
            "line": int(item.get("location", {}).get("row") or 1),
            "title": item.get("code", "Ruff finding"),
            "evidence": item.get("message", ""),
            "recommendation": "Fix the Ruff diagnostic or document why it is safe.",
            "confidence": 0.78,
        } for item in data if isinstance(item, dict)]
    if name == "detect-secrets":
        out = []
        for filename, items in data.get("results", {}).items():
            for item in items:
                out.append({
                    "scanner": name,
                    "rule_id": f"scanner.detect-secrets.{item.get('type', 'secret').lower().replace(' ', '-')}",
                    "severity": "critical",
                    "file": filename,
                    "line": int(item.get("line_number") or 1),
                    "title": item.get("type", "Secret detected"),
                    "evidence": "secret detected by detect-secrets",
                    "recommendation": "Remove the secret, rotate it, and use a managed secret store.",
                    "confidence": 0.95,
                })
        return out
    if name == "semgrep":
        return [{
            "scanner":
            name,
            "rule_id":
            f"scanner.semgrep.{item.get('check_id', 'issue')}",
            "severity":
            _severity(item.get("extra", {}).get("severity", "medium")),
            "file":
            item.get("path", ""),
            "line":
            int(item.get("start", {}).get("line") or 1),
            "title":
            item.get("check_id", "Semgrep finding"),
            "evidence":
            item.get("extra", {}).get("message", ""),
            "recommendation":
            item.get("extra", {}).get("message", "Review Semgrep finding and apply the recommended fix."),
            "confidence":
            0.86,
        } for item in data.get("results", [])]
    return []


def _severity(value: object) -> str:
    lowered = str(value).lower()
    if lowered in {"critical", "error"}:
        return "critical"
    if lowered in {"high"}:
        return "high"
    if lowered in {"low", "info", "warning"}:
        return "low" if lowered == "low" else "medium"
    return "medium"


if __name__ == "__main__":
    raise SystemExit(main())
