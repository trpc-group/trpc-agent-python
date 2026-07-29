#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

"""Integration tests for the A9 code-review Skill entry scripts."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from examples.skills_code_review_agent.code_review.skill_integrity import (
    canonical_source_sha256,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_ROOT = PROJECT_ROOT / "skills" / "code-review"
SCRIPTS_ROOT = SKILL_ROOT / "scripts"
MANIFEST_PATH = SCRIPTS_ROOT / "manifest.json"
RULES_ROOT = SKILL_ROOT / "rules"
REQUIRED_FINDING_FIELDS = {
    "severity",
    "category",
    "file",
    "line",
    "title",
    "evidence",
    "recommendation",
    "confidence",
    "source",
}


def _manifest() -> dict[str, object]:
    """读取测试目标 Skill 的执行 manifest。"""

    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _script_entry(manifest: dict[str, object], script_id: str) -> dict[str, object]:
    """按 script_id 返回一条已注册脚本定义。"""

    scripts = manifest["scripts"]
    assert isinstance(scripts, list)
    return next(entry for entry in scripts if entry["script_id"] == script_id)


def test_skill_documents_declare_workflow_capabilities_and_blind_spots() -> None:
    skill_text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    assert skill_text.startswith("---\nname: code-review\n")
    assert "description:" in skill_text.split("---", 2)[1]
    assert "scripts/manifest.json" in skill_text
    assert "## Inputs" in skill_text
    assert "## Review process" in skill_text
    assert "## Completion gate" in skill_text
    assert skill_text.count("**Complete when:**") == 6
    assert "read `references/security-boundaries.md` before" in " ".join(
        skill_text.split()
    )

    rule_ids = {
        "security.md": (
            "security.sql-fstring",
            "security.subprocess-shell-true",
            "security.dynamic-eval",
            "security.dynamic-exec",
            "security.os-system",
        ),
        "async-errors.md": (
            "async.blocking-time-sleep",
            "async.unawaited-coroutine",
        ),
        "resource-leak.md": (
            "resource.open-without-close",
            "resource.client-session-without-close",
        ),
        "missing-tests.md": ("tests.missing-coverage",),
        "secrets.md": ("secrets.",),
        "db-lifecycle.md": (
            "db.connection-without-close",
            "db.transaction-without-finalize",
        ),
    }
    for rule_name, expected_ids in rule_ids.items():
        rule_text = (RULES_ROOT / rule_name).read_text(encoding="utf-8").lower()
        assert "## detection contract" in rule_text
        assert "## scope and confidence" in rule_text
        assert "## examples" in rule_text
        assert "## remediation" in rule_text
        assert "## blind spots" in rule_text
        assert "### reports" in rule_text
        assert "### stays quiet" in rule_text
        assert all(rule_id in rule_text for rule_id in expected_ids)

    boundaries = (SKILL_ROOT / "references" / "security-boundaries.md").read_text(
        encoding="utf-8"
    ).lower()
    assert "## boundary map" in boundaries
    assert "## filter decision order" in boundaries
    assert "## runtime policy" in boundaries
    assert "## data handling" in boundaries
    assert "## failure semantics" in boundaries
    assert "## completion checklist" in boundaries
    assert "network policy is deny" in boundaries
    assert "sandbox output redaction" in boundaries
    assert "host field redaction" in boundaries
    assert "complete exit scan" in boundaries


def test_manifest_has_hashed_local_entries_and_fixed_budgets() -> None:
    manifest = _manifest()

    assert manifest["schema_version"] == "1.0.0"
    assert manifest["skill_name"] == "code-review"
    assert {entry["script_id"] for entry in manifest["scripts"]} == {
        "parse_diff",
        "run_checks",
    }
    for entry in manifest["scripts"]:
        assert set(entry) >= {
            "script_id",
            "entrypoint",
            "sha256",
            "files",
            "arguments",
            "timeout_seconds",
            "max_output_bytes",
            "requires_network",
        }
        entrypoint = (SCRIPTS_ROOT / entry["entrypoint"]).resolve()
        assert entrypoint.is_file()
        assert entrypoint.parent == SCRIPTS_ROOT.resolve()
        assert entry["sha256"] == canonical_source_sha256(entrypoint.read_bytes())
        integrity_files = entry["files"]
        assert isinstance(integrity_files, list)
        expected_paths = {
            path.relative_to(SCRIPTS_ROOT).as_posix()
            for path in SCRIPTS_ROOT.rglob("*.py")
        }
        assert {item["path"] for item in integrity_files} == expected_paths
        for item in integrity_files:
            source_path = (SCRIPTS_ROOT / item["path"]).resolve()
            source_path.relative_to(SCRIPTS_ROOT.resolve())
            assert item["sha256"] == canonical_source_sha256(source_path.read_bytes())
        assert entry["timeout_seconds"] == 30
        assert entry["max_output_bytes"] == 1024 * 1024
        assert entry["requires_network"] is False
        assert entry["arguments"] == {
            "type": "object",
            "additional_properties": False,
            "properties": {},
        }


def test_manifest_hash_is_stable_across_lf_and_crlf_checkouts() -> None:
    """验证 manifest 摘要不会因 Windows Git 换行转换而漂移。"""

    source = b"from __future__ import annotations\n\nvalue = 1\n"

    assert canonical_source_sha256(source) == canonical_source_sha256(
        source.replace(b"\n", b"\r\n")
    )


def test_registered_scripts_emit_sanitized_summary_and_findings(tmp_path: Path) -> None:
    workdir = tmp_path / "workspace"
    input_dir = workdir / "work" / "inputs"
    input_dir.mkdir(parents=True)
    token = "gh" + "p_" + ("a" * 36)
    diff = "\n".join(
        [
            "diff --git a/src/service.py b/src/service.py",
            "new file mode 100644",
            "--- /dev/null",
            "+++ b/src/service.py",
            "@@ -0,0 +1,2 @@",
            "+value = eval(payload)",
            f'+token = "{token}"',
        ]
    )
    (input_dir / "diff.json").write_text(
        json.dumps({"source_kind": "diff_file", "diff": diff}),
        encoding="utf-8",
    )
    manifest = _manifest()

    for script_id in ("parse_diff", "run_checks"):
        entry = _script_entry(manifest, script_id)
        completed = subprocess.run(
            [sys.executable, str(SCRIPTS_ROOT / entry["entrypoint"])],
            cwd=workdir,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
        assert token not in completed.stdout
        assert token not in completed.stderr

    parsed = (workdir / "out" / "parsed.json").read_text(encoding="utf-8")
    findings_text = (workdir / "out" / "findings.json").read_text(encoding="utf-8")
    assert token not in parsed
    assert token not in findings_text

    findings_payload = json.loads(findings_text)
    assert findings_payload["schema_version"] == "1.0.0"
    assert findings_payload["finding_count"] >= 2
    assert all(
        REQUIRED_FINDING_FIELDS <= set(finding)
        for finding in findings_payload["findings"]
    )
    assert any(
        finding["category"] == "secrets" for finding in findings_payload["findings"]
    )
    assert all(
        "[REDACTED:" in finding["evidence"] or "token" not in finding["evidence"]
        for finding in findings_payload["findings"]
    )
