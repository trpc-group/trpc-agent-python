"""保护: 所有数据集文件存在且非空."""

from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"

REQUIRED = [
    "trace/train.evalset.json",
    "trace/val_baseline.evalset.json",
    "trace/val_optimized.evalset.json",
    "live/train.evalset.json",
    "live/val.evalset.json",
]


def test_all_datasets_present_and_nonempty():
    for rel in REQUIRED:
        path = DATA_DIR / rel
        assert path.exists(), f"missing: {rel}"
        assert path.stat().st_size > 0, f"empty: {rel}"
