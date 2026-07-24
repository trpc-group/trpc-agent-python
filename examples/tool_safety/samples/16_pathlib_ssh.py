# Sample 16: pathlib chain into ~/.ssh.
from pathlib import Path
print(Path.home().joinpath(".ssh", "id_rsa").read_text())
