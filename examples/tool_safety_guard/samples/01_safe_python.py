from pathlib import Path

data = Path("input.txt").read_text(encoding="utf-8") if Path("input.txt").exists() else "hello"
print(data.upper())
