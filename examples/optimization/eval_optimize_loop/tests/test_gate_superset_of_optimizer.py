"""两层 metric 包含关系: gate_metrics 必须 ⊇ optimizer.metrics."""

import json
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"


def test_gate_metrics_is_superset_of_optimizer_metrics():
    gate = {m["metric_name"] for m in json.loads((DATA_DIR / "gate_metrics.json").read_text())["metrics"]}
    opt = {m["metric_name"] for m in json.loads((DATA_DIR / "optimizer.json").read_text())["evaluate"]["metrics"]}
    assert opt <= gate, f"optimizer metric {opt - gate} not in gate_metrics"
