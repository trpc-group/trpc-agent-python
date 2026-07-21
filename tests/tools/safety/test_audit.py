import json
import os
from concurrent.futures import ThreadPoolExecutor

from trpc_agent_sdk.tools.safety._audit import JsonlAuditSink
from trpc_agent_sdk.tools.safety._audit import record_audit_event
from trpc_agent_sdk.tools.safety._models import RiskLevel
from trpc_agent_sdk.tools.safety._models import SafetyAuditEvent
from trpc_agent_sdk.tools.safety._models import SafetyDecision


def _event(tool_name: str) -> SafetyAuditEvent:
    return SafetyAuditEvent(
        tool_name=tool_name,
        decision=SafetyDecision.DENY,
        risk_level=RiskLevel.HIGH,
        rule_ids=["TEST001"],
        duration_ms=0.5,
        redacted=True,
        blocked=True,
        script_sha256="a" * 64,
        policy_version="1",
    )


def test_jsonl_audit_sink_writes_parseable_lines_concurrently(tmp_path):
    path = tmp_path / "nested" / "audit.jsonl"
    sink = JsonlAuditSink(path)
    events = [_event(f"tool-{index}") for index in range(100)]

    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(sink.record, events))

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 100
    assert {record["tool_name"] for record in records} == {f"tool-{index}" for index in range(100)}
    assert all(record["redacted"] is True for record in records)


def test_audit_callback_protocol_receives_event():
    received = []
    event = _event("callback-tool")

    record_audit_event(received.append, event)

    assert received == [event]


def test_audit_failure_is_best_effort():

    class BrokenSink:

        def record(self, event):
            del event
            raise OSError("disk full")

    record_audit_event(BrokenSink(), _event("still-denied"))


def test_jsonl_audit_sink_completes_short_writes(tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    original_write = os.write

    def short_write(fd, payload):
        chunk_size = max(1, len(payload) // 2)
        return original_write(fd, payload[:chunk_size])

    monkeypatch.setattr("trpc_agent_sdk.tools.safety._audit.os.write", short_write)

    JsonlAuditSink(path).record(_event("short-write"))

    assert json.loads(path.read_text(encoding="utf-8"))["tool_name"] == "short-write"
