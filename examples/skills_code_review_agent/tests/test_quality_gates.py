# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Business quality gates for deterministic review and redaction behavior."""

from __future__ import annotations

import json
from pathlib import Path

from examples.skills_code_review_agent.agent import FindingCategory
from examples.skills_code_review_agent.agent import FindingSeverity
from examples.skills_code_review_agent.agent import InputType
from examples.skills_code_review_agent.agent import ReviewPipelineConfig
from examples.skills_code_review_agent.agent import ReviewStore
from examples.skills_code_review_agent.agent import parse_unified_diff
from examples.skills_code_review_agent.agent import redact_text
from examples.skills_code_review_agent.agent import run_review_pipeline
from examples.skills_code_review_agent.agent import run_review_rules
from examples.skills_code_review_agent.tests.secret_samples import aws_access_key_like_token
from examples.skills_code_review_agent.tests.secret_samples import aws_session_key_like_token
from examples.skills_code_review_agent.tests.secret_samples import bearer_like_token
from examples.skills_code_review_agent.tests.secret_samples import forbidden_provider_literals
from examples.skills_code_review_agent.tests.secret_samples import generic_api_key_value
from examples.skills_code_review_agent.tests.secret_samples import generic_bearer_value
from examples.skills_code_review_agent.tests.secret_samples import generic_password_value
from examples.skills_code_review_agent.tests.secret_samples import generic_secret_value
from examples.skills_code_review_agent.tests.secret_samples import github_classic_like_token
from examples.skills_code_review_agent.tests.secret_samples import github_oauth_like_token
from examples.skills_code_review_agent.tests.secret_samples import github_pat_like_token
from examples.skills_code_review_agent.tests.secret_samples import jwt_like_token
from examples.skills_code_review_agent.tests.secret_samples import openai_like_token
from examples.skills_code_review_agent.tests.secret_samples import openai_live_like_token
from examples.skills_code_review_agent.tests.secret_samples import slack_like_token


def test_high_risk_recall_and_false_positive_quality_gate():
    # Provider-like token samples are assembled at runtime so tracked tests stay
    # compatible with secret scanning while still exercising the same regexes.
    positives = [
        ("secret.py", [f"API_KEY = '{openai_like_token()}'"], FindingCategory.SECRET),
        ("exec.py", ["result = eval(user_input)"], FindingCategory.SECURITY),
        ("exec.py", ["exec(user_code)"], FindingCategory.SECURITY),
        ("shell.py", ["subprocess.run(cmd, shell=True)"], FindingCategory.SECURITY),
        ("tokens.py", [f"password = '{generic_password_value()}'"], FindingCategory.SECRET),
    ]
    hits = 0
    for path, lines, category in positives:
        findings = run_review_rules(_summary(path, lines))
        high_risk = [
            item for item in findings
            if item.category is category and item.severity in {FindingSeverity.HIGH, FindingSeverity.CRITICAL}
        ]
        hits += int(bool(high_risk))

    negatives = [
        ("files.py", ["with open('safe.txt') as handle:", "    data = handle.read()"]),
        ("async_client.py", [
            "async def fetch(url):",
            "    async with httpx.AsyncClient() as client:",
            "        return await client.get(url)",
        ]),
        ("db.py", ["with sqlite3.connect(path) as conn:", "    conn.execute('select 1')"]),
        ("app.py", ["message = 'token bucket rate limiter'"]),
        ("tests/test_app.py", ["assert normalize('value') == 'value'"]),
    ]
    unexpected = []
    for path, lines in negatives:
        unexpected.extend([
            item for item in run_review_rules(_summary(path, lines))
            if item.confidence >= 0.7 and item.category in {FindingCategory.SECURITY, FindingCategory.SECRET}
        ])

    recall = hits / len(positives)
    high_confidence_total = hits + len(unexpected)
    false_positive_rate = len(unexpected) / high_confidence_total if high_confidence_total else 0.0

    assert recall >= 0.80
    assert false_positive_rate <= 0.15


def test_redaction_quality_gate_covers_common_secret_shapes():
    cases = [
        (f"Authorization: Bearer {bearer_like_token()}", [bearer_like_token()]),
        (f"api_key={openai_like_token()}", [openai_like_token()]),
        (f"OPENAI_API_KEY='{openai_live_like_token()}'", [openai_live_like_token()]),
        ("password=supersecret", ["supersecret"]),
        ("passwd: hunter2", ["hunter2"]),
        ("pwd='local-password'", ["local-password"]),
        (f"secret = '{generic_secret_value()}'", [generic_secret_value()]),
        (f"token={github_pat_like_token()}", [github_pat_like_token()]),
        (f"token={github_classic_like_token()}", [github_classic_like_token()]),
        (f"token={github_oauth_like_token()}", [github_oauth_like_token()]),
        (f"slack_token={slack_like_token()}", [slack_like_token()]),
        (f"aws_key={aws_access_key_like_token()}", [aws_access_key_like_token()]),
        (f"aws_session={aws_session_key_like_token()}", [aws_session_key_like_token()]),
        ("DATABASE_URL=postgres://user:plainpass@db/app", ["plainpass"]),
        ("mysql://root:rootpass@localhost/db", ["rootpass"]),
        ("redis://default:redispass@localhost:6379/0", ["redispass"]),
        ("private_key=-----BEGIN PRIVATE KEY-----\\nbody\\n-----END PRIVATE KEY-----", ["body"]),
        ("client_secret=abc123456789", ["abc123456789"]),
        ("refresh-token=rt_abcdefghijklmnop", ["rt_abcdefghijklmnop"]),
        (f"jwt_token={jwt_like_token()}", [jwt_like_token().split('.')[0]]),
    ]

    redacted_count = 0
    for text, forbidden_values in cases:
        redacted = redact_text(text)
        if "[REDACTED]" in redacted and all(value not in redacted for value in forbidden_values):
            redacted_count += 1

    assert redacted_count / len(cases) >= 0.95


def test_secret_fixture_does_not_leak_known_values_to_report_or_store(tmp_path: Path):
    result = run_review_pipeline(
        ReviewPipelineConfig(
            input_type=InputType.FIXTURE,
            input_ref="secret",
            output_dir=tmp_path,
            db_path=tmp_path / "review.sqlite3",
        ))
    forbidden = [
        generic_api_key_value(),
        generic_password_value(),
        generic_bearer_value(),
    ]
    report_text = json.dumps(result.report.to_dict())
    assert all(value not in report_text for value in forbidden)

    with ReviewStore(tmp_path / "review.sqlite3") as store:
        stored_text = json.dumps({
            "findings": store.list_findings(result.task.id),
            "filters": store.list_filter_events(result.task.id),
            "sandbox": store.list_sandbox_runs(result.task.id),
            "report": store.get_report(result.task.id),
        })
    assert all(value not in stored_text for value in forbidden)


def test_curated_secret_samples_do_not_store_blocked_provider_literals():
    tracked = Path(__file__).read_text(encoding="utf-8")
    helper = (Path(__file__).parent / "secret_samples.py").read_text(encoding="utf-8")
    fixture = (Path(__file__).parents[1] / "fixtures" / "secret.diff").read_text(encoding="utf-8")

    for blocked in forbidden_provider_literals():
        assert blocked not in tracked
        assert blocked not in helper
        assert blocked not in fixture


def _summary(path: str, added_lines: list[str]):
    return parse_unified_diff(
        _diff(path, added_lines),
        task_id=f"quality-{path}",
        input_type=InputType.DIFF_FILE,
        input_ref=f"<quality:{path}>",
    )


def _diff(path: str, added_lines: list[str]) -> str:
    rendered = "\n".join(f"+{line}" for line in added_lines)
    line_count = max(len(added_lines), 1)
    return f"""diff --git a/{path} b/{path}
--- a/{path}
+++ b/{path}
@@ -0,0 +1,{line_count} @@
{rendered}
"""
