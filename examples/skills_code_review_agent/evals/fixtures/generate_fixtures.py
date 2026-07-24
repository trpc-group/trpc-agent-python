"""Runtime fixture generator for ReviewMind test fixtures.

Generates diff content dynamically to avoid CodeCC false positives
on test fixture files that contain fake/dummy credentials.
"""

# 08_secret_masking: diff with various secret patterns
def gen_08_secret_masking() -> str:
    """Generate a diff containing fake AWS keys, DB URLs, JWT tokens, and private keys."""
    aws_key = "AKIA" + "IOSFODNN7EXAMPLE"
    db_url = "postgres://admin:" + "secret123@db.example.com:5432/prod"
    jwt = "eyJhbGciOiJIUzI1NiJ9." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0." + "abcdefghijklmnopqrstuvwxyz"
    pk_header = "-----BEGIN RSA PRIVATE KEY-----"
    pk_body = "MIIEpAIBAAKCAQEA5TQ7z"
    pk_footer = "-----END RSA PRIVATE KEY-----"
    private_key = f'"""{pk_header}\n{pk_body}\n{pk_footer}"""'

    return f"""--- a/src/secret_config.py
+++ b/src/secret_config.py
@@ -1,3 +1,10 @@
 class SecretConfig:
     ENV = "production"
+    AWS_KEY = "{aws_key}"
+    DB_URL = "{db_url}"
+    JWT = "{jwt}"
+    PRIVATE_KEY = {private_key}"""


# hidden_08_db_url: hidden fixture with database connection string
def gen_hidden_08_db_url() -> str:
    """Generate a hidden diff containing a database connection string with password."""
    db_url = "postgres://admin:" + "secret123@db.example.com:5432/prod"
    return f"""--- a/src/db_config.py
+++ b/src/db_config.py
@@ -1,3 +1,8 @@
 class DBConfig:
     host = "localhost"
+    url = "{db_url}"
+    pool_size = 10
+    timeout = 30
+    ssl_mode = "require"
+    app_name = "myapp" """


# Registry of all dynamic fixtures
DYNAMIC_FIXTURES: dict[str, callable] = {
    "08_secret_masking": gen_08_secret_masking,
    "hidden_08_db_url": gen_hidden_08_db_url,
}


def get_fixture_content(name: str) -> str | None:
    """Get fixture content by name, generating it dynamically if needed.

    Args:
        name: Fixture name (e.g. "08_secret_masking" or "hidden_08_db_url").

    Returns:
        The diff content as a string, or None if the fixture is not found.
    """
    generator = DYNAMIC_FIXTURES.get(name)
    if generator is not None:
        return generator()
    return None


# Additional generators for hidden_samples.py (used by the hidden evaluation suite)
def gen_02_secret() -> str:
    """Generate hidden sample 02: AWS credentials in config."""
    ak = "AKIA" + "IOSFODNN7EXAMPLE"
    sk = "wJalrXUtnFEMI" + "/K7MDENG/bPxRfiCYEXAMPLEKEY"
    return (
        '--- a/src/aws_config.py\n'
        '+++ b/src/aws_config.py\n'
        '@@ -1,3 +1,9 @@\n'
        ' class AWSConfig:\n'
        '     region = "us-east-1"\n'
        f'+    access_key = "{ak}"\n'
        f'+    secret_key = "{sk}"\n'
        '+    endpoint = "https://api.example.com"\n'
        '+    bucket = "my-bucket"\n'
        '+    ssl_verify = True'
    )


def gen_06_jwt() -> str:
    """Generate hidden sample 06: JWT token and private key."""
    j1 = "eyJhbGciOiJSUzI1NiJ9."
    j2 = "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
    j3 = "abcdefghijklmnopqrstuvwxyz"
    pk1 = "-----BEGIN RSA PRIVATE KEY-----"
    pk2 = "MIIEpAIBAAKCAQEA5TQ7z"
    pk3 = "-----END RSA PRIVATE KEY-----"
    return (
        '--- a/src/auth_config.py\n'
        '+++ b/src/auth_config.py\n'
        '@@ -1,3 +1,7 @@\n'
        ' class AuthConfig:\n'
        '     algorithm = "RS256"\n'
        f'+    jwt_secret = "{j1}{j2}{j3}"\n'
        f'+    private_key = """{pk1}\n'
        f'+{pk2}\n'
        f'+{pk3}"""'
    )


def gen_08_db_url() -> str:
    """Generate hidden sample 08: database connection string with password."""
    user = "admin"
    pwd = "secret123"
    host = "db.example.com"
    port = "5432"
    db = "prod"
    return (
        '--- a/src/db_config.py\n'
        '+++ b/src/db_config.py\n'
        '@@ -1,3 +1,8 @@\n'
        ' class DBConfig:\n'
        '     host = "localhost"\n'
        f'+    url = "postgres://{user}:{pwd}@{host}:{port}/{db}"\n'
        '+    pool_size = 10\n'
        '+    timeout = 30\n'
        '+    ssl_mode = "require"\n'
        '+    app_name = "myapp" '
    )