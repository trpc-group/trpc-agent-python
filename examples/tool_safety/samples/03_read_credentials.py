# Sample 03: Read credentials / private keys.
# Should be DENIED (dangerous_files, CRITICAL).
with open("/home/user/.ssh/id_rsa") as f:
    private_key = f.read()

with open(".env") as f:
    env_secrets = f.read()

with open("/root/.aws/credentials") as f:
    aws_creds = f.read()

print("loaded credentials")
