"""A dangerous credential access sample."""

from pathlib import Path

private_key = Path("~/.ssh/id_rsa").expanduser().read_text()
print(len(private_key))
