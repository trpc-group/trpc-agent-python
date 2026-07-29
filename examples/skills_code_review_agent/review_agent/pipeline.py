# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""End-to-end review pipeline: parse -> persist -> agent(skill/sandbox) -> triage -> report.

This module owns the task lifecycle.  Failure philosophy: a sandbox timeout,
a filter denial or a crashed check degrades the task to ``partial`` with the
reason recorded — it never crashes the review run itself.
"""

from __future__ import annotations

import json
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from trpc_agent_sdk.configs import RunConfig
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, Part

from .agent import build_review_agent, run_llm_review
from .config import has_real_model
from .diff_parser import ParsedInput
from .findings import TriageResult, triage
from .metrics import MetricsCollector
from .redactor import Redactor
from .report import ReportInputs, build_report_payload, write_reports
from .review_filter import FilterPolicy, FilterRecorder, FilterState
from .sandbox import create_sandbox
from .store import DiffFile, Report, ReviewStore, ReviewTask, SandboxRun, digest


@dataclass
class ReviewOptions:
    """CLI-facing knobs."""

    db_url: str = "sqlite:///review.db"
    output_dir: str = "."
    unsafe_local: bool = False
    dry_run: bool = False
    run_timeout: int = 60
    inject_sleep: float = 0.0  # fixture 07 fault injection
    docker_image: Optional[str] = None
    #: how the LLM participates: "agent" = the model drives the tool loop;
    #: "hybrid" = the scripted FakeModel drives the sandbox and the model does
    #: one re-judgement call afterwards (deterministic sandbox channel);
    #: "off" = static only.  "auto" = agent when a model is configured.
    llm_mode: str = "auto"


@dataclass
class ReviewOutcome:
    task_id: str
    status: str
    report_json_path: str
    report_md_path: str
    payload: dict


def _extract_llm_review(text: str) -> tuple[list[dict], list[dict], str]:
    """Parse the model's final JSON message; empty on any mismatch.

    Models often wrap the JSON in prose or code fences, and reasoning text
    may itself contain ``{``.  Scan every opening brace and take the first
    position that yields a valid object with the expected keys.
    """
    if not text:
        return [], [], ""
    decoder = json.JSONDecoder()
    for pos, char in enumerate(text):
        if char != "{":
            continue
        try:
            data, _end = decoder.raw_decode(text, pos)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        if not ({"verdicts", "additional_findings", "summary"} & set(data)):
            continue  # some other JSON object embedded in prose
        verdicts = data.get("verdicts") or []
        additional = data.get("additional_findings") or []
        summary = str(data.get("summary") or "")
        if isinstance(verdicts, list) and isinstance(additional, list):
            return verdicts, additional, summary
    return [], [], ""


def _sandbox_runs_from_events(task_id: str, runtime_kind: str, tool_events: list[dict]) -> list[SandboxRun]:
    rows = []
    for event in tool_events:
        response = event["response"]
        warnings = response.get("warnings") or []
        timed_out = bool(response.get("timed_out"))
        status = "timeout" if timed_out else ("ok" if response.get("exit_code") == 0 else "error")
        rows.append(
            SandboxRun(
                task_id=task_id,
                tool=event["name"],
                command=str(event["args"].get("command", ""))[:500],
                runtime=runtime_kind,
                status=status,
                exit_code=int(response.get("exit_code") or 0),
                duration_ms=int(response.get("duration_ms") or 0),
                timed_out=timed_out,
                truncated=any("truncated" in warning for warning in warnings),
                stdout_digest=digest(str(response.get("stdout", ""))),
                stderr_digest=digest(str(response.get("stderr", ""))),
            ))
    return rows


async def run_review(parsed: ParsedInput, options: ReviewOptions) -> ReviewOutcome:
    """Run the complete review pipeline for one parsed input."""
    task_id = uuid.uuid4().hex[:12]
    store = ReviewStore(options.db_url)
    await store.init()
    redactor = Redactor()
    collector = MetricsCollector(task_id=task_id)
    notes: list[str] = []

    # resolve LLM participation: which model drives the tool loop, and
    # whether a separate one-shot re-judgement call runs afterwards
    llm_mode = options.llm_mode
    if options.dry_run or not has_real_model():
        if llm_mode not in ("off", "auto"):
            notes.append(f"llm_mode={llm_mode} requested but no model configured; static only")
        llm_mode = "off"
    elif llm_mode == "auto":
        llm_mode = "agent"
    dry_run = llm_mode != "agent"  # FakeModel drives the tool loop unless the LLM does
    if llm_mode == "off" and not options.dry_run and not has_real_model():
        notes.append("no model configured; fell back to dry-run (FakeModel drives the tool loop)")

    collector.phase("persist_input")
    parsed.payload["task_id"] = task_id
    task = ReviewTask(
        id=task_id,
        status="running",
        input_type=parsed.input_type,
        input_ref=parsed.input_ref[:500],
        diff_digest=parsed.diff_digest,
        mode=parsed.mode,
        dry_run=(llm_mode == "off"),
        config_json={
            "run_timeout": options.run_timeout,
            "unsafe_local": options.unsafe_local,
            "inject_sleep": options.inject_sleep,
            "llm_mode": llm_mode,
        },
    )
    await store.add(task)
    await store.add_all([DiffFile(task_id=task_id, **summary) for summary in parsed.file_summaries])

    # stage review_input.json in a per-task directory; the filter confines
    # host:// inputs to exactly this directory
    staging = Path(tempfile.mkdtemp(prefix=f"cr-input-{task_id}-"))
    if options.inject_sleep > 0:
        parsed.payload["debug"] = {"sleep_seconds": options.inject_sleep}
    input_path = staging / "review_input.json"
    input_path.write_text(json.dumps(parsed.payload, ensure_ascii=False), encoding="utf-8")

    collector.phase("sandbox_setup")
    sandbox = create_sandbox(
        prefer="local" if options.unsafe_local else "container",
        work_root=str(staging / "workspaces"),
        inputs_host_base=str(staging),
        docker_image=options.docker_image,
    )
    task.runtime = sandbox.kind
    await store.update_task(task_id, runtime=sandbox.kind)
    if sandbox.kind == "local" and not options.unsafe_local:
        notes.append(f"sandbox degraded to local: {sandbox.reason}")

    policy = FilterPolicy(
        allowed_input_prefixes=(str(staging), ),
        max_timeout_s=options.run_timeout,
    )
    state = FilterState()
    recorder = FilterRecorder(store, task_id, state)

    diff_excerpt = ""
    if llm_mode != "off":
        # the model re-judges findings against the diff text (bounded)
        chunks = []
        for file_entry in parsed.payload["files"]:
            content = file_entry.get("content")
            if content:
                chunks.append(f"--- {file_entry['path']} ---\n{content}")
        diff_excerpt = "\n".join(chunks)[:60_000]

    agent, _repository = build_review_agent(
        sandbox=sandbox,
        recorder=recorder,
        policy=policy,
        redactor=redactor,
        dry_run=dry_run,
        input_src=f"host://{input_path}",
        run_timeout=options.run_timeout,
        diff_excerpt=diff_excerpt,
        on_model_response=collector.observe_model_response,
    )

    collector.phase("agent_loop")
    runner = Runner(app_name="code_review_agent",
                    agent=agent,
                    session_service=InMemorySessionService(),
                    enable_post_turn_processing=False)
    final_text = ""
    skill_run_events: list[dict] = []
    pending_calls: dict[str, dict] = {}
    agent_error = ""
    model_errors: list[str] = []
    try:
        # non-streaming events: a batch CLI has nobody to stream to
        async for event in runner.run_async(
                user_id="reviewer",
                session_id=f"review-{task_id}",
                new_message=Content(role="user",
                                    parts=[Part(text="Review the staged diff following your instructions.")]),
                run_config=RunConfig(streaming=False),
        ):
            collector.observe_event(event)
            if event.error_code:
                model_errors.append(f"{event.error_code}: {str(event.error_message or '')[:200]}")
            for call in event.get_function_calls() or []:
                if call.name == "skill_run":
                    pending_calls[call.id or "last"] = dict(call.args or {})
            for response in event.get_function_responses() or []:
                if response.name == "skill_run" and isinstance(response.response, dict) \
                        and "exit_code" in response.response:
                    args = pending_calls.get(response.id or "last", {}) or pending_calls.get("last", {})
                    skill_run_events.append({"name": "skill_run", "args": args, "response": response.response})
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text and not getattr(part, "thought", False):
                        final_text = part.text
    except Exception as ex:  # pylint: disable=broad-except
        agent_error = f"{type(ex).__name__}: {str(ex)[:300]}"
        collector.record_error(f"agent:{type(ex).__name__}")
        logger.error("agent loop failed: %s", agent_error, exc_info=True)
    finally:
        try:
            await runner.close()
        except Exception:  # pylint: disable=broad-except
            pass

    collector.phase("collect_findings")
    static_findings: list[dict] = []
    static_stats: dict = {}
    sandbox_failed_reason = ""
    for event in skill_run_events:
        response = event["response"]
        payload_text = ""
        primary = response.get("primary_output") or {}
        if primary.get("name", "").endswith("findings.json") and primary.get("content"):
            payload_text = primary["content"]
        else:
            for file_entry in response.get("output_files") or []:
                if str(file_entry.get("name", "")).endswith("findings.json") and file_entry.get("content"):
                    payload_text = file_entry["content"]
                    break
        if payload_text:
            try:
                data = json.loads(payload_text)
                static_findings.extend(data.get("findings") or [])
                static_stats = data.get("stats") or {}
            except ValueError as ex:
                sandbox_failed_reason = f"findings.json unparsable: {ex}"
        elif response.get("timed_out"):
            sandbox_failed_reason = "sandbox execution timed out"
        elif response.get("exit_code") != 0:
            sandbox_failed_reason = f"sandbox exited with code {response.get('exit_code')}"

    if not skill_run_events and not agent_error:
        blocked = [event for event in state.events if event["decision"] != "allow"]
        if blocked:
            sandbox_failed_reason = f"execution blocked by filter: {blocked[-1]['rule']}"
        elif model_errors:
            sandbox_failed_reason = f"model error before any sandbox run: {model_errors[-1]}"
        else:
            sandbox_failed_reason = "agent finished without any sandbox execution"

    for error in static_stats.get("errors", []) or []:
        notes.append(f"check degraded: {error.get('check')}: {error.get('error')}")
        collector.record_error(f"check:{error.get('check')}")

    llm_verdicts: list[dict] = []
    llm_additional: list[dict] = []
    if llm_mode == "hybrid":
        collector.phase("llm_review")
        try:
            final_text = await run_llm_review(static_findings, diff_excerpt, collector.observe_model_response)
        except Exception as ex:  # pylint: disable=broad-except
            collector.record_error(f"llm_review:{type(ex).__name__}")
            notes.append(f"llm re-judgement failed ({type(ex).__name__}); static-only triage")
            final_text = ""
    if llm_mode != "off":
        llm_verdicts, llm_additional, llm_summary = _extract_llm_review(final_text)
        if llm_summary:
            notes.append(f"llm summary: {llm_summary[:300]}")
        if not llm_verdicts and final_text:
            notes.append("llm final message was not valid JSON; falling back to static-only triage")

    diff_text_for_quotes = "\n".join(file_entry.get("content") or "" for file_entry in parsed.payload["files"])
    triage_result: TriageResult = triage(
        task_id=task_id,
        static_findings=static_findings,
        llm_verdicts=llm_verdicts,
        llm_additional=llm_additional,
        diff_text=diff_text_for_quotes,
        redactor=redactor,
        llm_ran=bool(llm_verdicts),
    )

    collector.phase("persist_findings")
    await store.insert_findings(triage_result.all_rows)
    sandbox_rows = _sandbox_runs_from_events(task_id, sandbox.kind, skill_run_events)
    if sandbox_rows:
        await store.add_all(sandbox_rows)

    # task status: failed only for infrastructure errors before any result;
    # degraded-but-reported runs are "partial"; clean full runs "succeeded"
    if agent_error and not static_findings:
        status = "failed"
        task_error = agent_error
    elif sandbox_failed_reason or agent_error or static_stats.get("errors"):
        status = "partial"
        task_error = sandbox_failed_reason or agent_error or "some checks degraded"
        if sandbox_failed_reason:
            notes.append(f"sandbox: {sandbox_failed_reason}")
    else:
        status = "succeeded"
        task_error = ""

    collector.phase("render_report")
    filter_blocks = sum(1 for event in state.events if event["decision"] != "allow")
    metrics_row = collector.finish(
        filter_blocks=filter_blocks,
        finding_count=len(triage_result.reported),
        severity_dist=triage_result.severity_dist(),
        sandbox_ms_fallback=sum(row.duration_ms for row in sandbox_rows),
    )
    await store.add(metrics_row)

    task.status = status
    task.error = task_error
    from datetime import datetime
    await store.update_task(task_id, status=status, error=task_error, finished_at=datetime.now())

    report_inputs = ReportInputs(
        task=task,
        triage=triage_result,
        filter_events=state.events,
        sandbox_runs=sandbox_rows,
        metrics_row=metrics_row,
        notes=notes,
    )
    payload = build_report_payload(report_inputs)
    # final safety net: no plaintext secret may survive into the report
    payload = redactor.redact_obj(payload)
    json_path, md_path = write_reports(payload, options.output_dir)
    from .report import render_markdown
    await store.add_all([
        Report(task_id=task_id,
               format="json",
               content=json.dumps(payload, ensure_ascii=False),
               summary_json=payload["summary"]),
        Report(task_id=task_id, format="md", content=render_markdown(payload), summary_json=None),
    ])
    await store.close()

    return ReviewOutcome(task_id=task_id,
                         status=status,
                         report_json_path=json_path,
                         report_md_path=md_path,
                         payload=payload)
