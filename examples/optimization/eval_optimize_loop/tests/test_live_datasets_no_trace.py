"""保护: live 数据集不能含 trace cases (RemoteEvalService 会拒绝)."""

import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"


def test_live_datasets_have_no_trace_cases():
    for name in ["train.evalset.json", "val.evalset.json"]:
        path = DATA_DIR / "live" / name
        data = json.loads(path.read_text(encoding="utf-8"))
        for case in data["eval_cases"]:
            assert case.get("eval_mode") != "trace", \
                f"{name} contains trace case {case['eval_id']}"
            assert "actual_conversation" not in case, \
                f"{name}/{case['eval_id']} has actual_conversation (trace-only field)"
