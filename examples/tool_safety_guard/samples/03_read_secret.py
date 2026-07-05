from pathlib import Path

print(Path("~/.ssh/id_rsa").expanduser().read_text(encoding="utf-8"))
