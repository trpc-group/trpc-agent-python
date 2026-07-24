from pathlib import Path

secret = (Path.home() / ".ssh" / "id_rsa").read_text()
