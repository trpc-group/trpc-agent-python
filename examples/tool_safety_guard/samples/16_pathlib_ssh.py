from pathlib import Path
ssh_key = Path.home() / '.ssh' / 'id_rsa'
content = ssh_key.read_text()
print(content[:10])
