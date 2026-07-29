from pathlib import Path

key_path = "~/.ssh/id_rsa"
private_key = Path(key_path).read_text()
