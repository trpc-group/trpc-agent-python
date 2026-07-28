from pathlib import Path

Path("huge.bin").write_text("x" * 200000000)
