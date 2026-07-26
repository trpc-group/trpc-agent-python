from pathlib import Path

print(Path("~/.ssh/id_rsa").read_text())
