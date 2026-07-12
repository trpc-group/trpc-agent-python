"""Prompt-sensitive fake behavior generator; never reads eval IDs."""

from __future__ import annotations

import re

from trpc_agent_sdk.evaluation import EvalCase, Invocation


def user_text(case: EvalCase) -> str:
    return " ".join(part.text or "" for part in case.conversation[0].user_content.parts).lower()


def generate_trace(case: EvalCase, prompts: dict[str, str]) -> EvalCase:
    """Generate behavior from user semantics and prompt rules."""
    changed = case.model_copy(deep=True)
    text = user_text(case)
    router = prompts.get("router_prompt", "")
    skill = prompts.get("skill_prompt", "")
    system = prompts.get("system_prompt", "")
    tool_name = None
    tool_args = None
    if "shipping" in text or "shipment" in text:
        tracking = re.search(r"t-\d+", text).group(0).upper()
        tool_name, tool_args = "track_shipment", {"tracking_id": tracking}
        response = f'{{"tracking_id":"{tracking}","status":"in_transit"}}'
    elif "billing" in text or "invoice" in text:
        invoice = re.search(r"i-\d+", text).group(0).upper()
        tool_name = "create_refund" if "BILLING_TO_REFUND=ON" in router else "get_invoice"
        tool_args = {"invoice_id": invoice}
        response = f'{{"invoice_id":"{invoice}","status":"open"}}'
    else:
        order_match = re.search(r"o-\d+", text)
        if order_match is None:
            response = '{"status":"unsupported"}'
            raw = {
                "user_content": case.conversation[0].user_content.model_dump(mode="json"),
                "final_response": {"role": "model", "parts": [{"text": response}]},
            }
            changed.actual_conversation = [Invocation.model_validate(raw)]
            return changed
        order = order_match.group(0).upper()
        if "summarize" in text:
            response = (
                f'{{"status":"pending","order_id":"{order}"}}'
                if "JSON_STATUS=ALWAYS_REQUIRED" in system
                else f'{{"order_id":"{order}"}}'
            )
        else:
            reason = "duplicate" if "duplicate" in text else "damaged"
            strict = "REFUND_ROUTE=STRICT" in router
            tool_name = "create_refund" if strict or reason == "duplicate" else "get_invoice"
            tool_args = {"order_id": order}
            if "REFUND_REASON=REQUIRED" in skill or reason == "damaged":
                tool_args["reason"] = reason
            response = f'{{"status":"submitted","order_id":"{order}"}}'
    raw = {
        "user_content": case.conversation[0].user_content.model_dump(mode="json"),
        "final_response": {"role": "model", "parts": [{"text": response}]},
    }
    if tool_name:
        raw["intermediate_data"] = {"tool_uses": [{"name": tool_name, "args": tool_args}]}
    changed.actual_conversation = [Invocation.model_validate(raw)]
    return changed
