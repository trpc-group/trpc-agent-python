from pathlib import Path

name = ".env"
print(Path("/tmp") / name)
open(Path.home() / ".ssh" / "id_rsa").read()
