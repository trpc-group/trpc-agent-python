# Static scanner sample; do not execute.

from pathlib import Path

private_key = Path("~/.ssh/id_rsa").read_text(encoding="utf-8")
print(len(private_key))
