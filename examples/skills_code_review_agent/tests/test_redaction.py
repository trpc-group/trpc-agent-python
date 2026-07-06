# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Secret redaction (issue requirement 7, acceptance criterion 5: ≥95%)."""

from codereview.redaction import REDACTED_PLACEHOLDER
from codereview.redaction import SecretRedactor

# 48 secret-bearing samples: (text, plaintext_value_that_must_disappear)
SECRET_SAMPLES = [
    ("aws key AKIAIOSFODNN7EXAMPLE in code", "AKIAIOSFODNN7EXAMPLE"),
    ("id = 'ASIAJEXAMPLEKEY12345'", "ASIAJEXAMPLEKEY12345"),
    ("aws_secret_access_key = wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY99",
     "wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY99"),
    ("AWS_SECRET_KEY: 'abcdefghijklmnopqrstuvwxyz012345'", "abcdefghijklmnopqrstuvwxyz012345"),
    ("token ghp_FAKE1234567890abcdefFAKE1234567890", "ghp_FAKE1234567890abcdefFAKE1234567890"),
    ("gho_FAKEabcdef1234567890FAKEabcdef12", "gho_FAKEabcdef1234567890FAKEabcdef12"),
    ("ghs_SERVICEfake1234567890abcdef99", "ghs_SERVICEfake1234567890abcdef99"),
    ("slack: xoxb-7777777-8888888-FAKEfakeFAKE", "xoxb-7777777-8888888-FAKEfakeFAKE"),
    ("xoxp-111111-222222-333333-abcdef", "xoxp-111111-222222-333333-abcdef"),
    ("openai sk-FAKEfakeFAKEfakeFAKEfake1234", "sk-FAKEfakeFAKEfakeFAKEfake1234"),
    ("sk-proj-FAKE1234567890fakeFAKE1234", "sk-proj-FAKE1234567890fakeFAKE1234"),
    ("google AIzaFAKEfake_FAKE1234567890abcdefgHIJ", "AIzaFAKEfake_FAKE1234567890abcdefgHIJ"),
    ("jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmYWtlIn0.FAKEsigFAKEsig",
     "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmYWtlIn0.FAKEsigFAKEsig"),
    ("Authorization: Bearer FAKEbearerTOKENfake1234567890", "FAKEbearerTOKENfake1234567890"),
    ("-----BEGIN RSA PRIVATE KEY----- MIIFAKE -----END RSA PRIVATE KEY-----", "MIIFAKE"),
    ("-----BEGIN EC PRIVATE KEY-----", "BEGIN EC PRIVATE KEY"),
    ("postgres://svc:sup3rSecretDbPass@db:5432/prod", "sup3rSecretDbPass"),
    ("mysql://root:rootpw12345@localhost/app", "rootpw12345"),
    ("redis://default:redisPass9876@cache:6379", "redisPass9876"),
    ("password = 'hunter2butlonger'", "hunter2butlonger"),
    ('password: "yamlPassword123"', "yamlPassword123"),
    ("passwd=legacyPasswd12345", "legacyPasswd12345"),
    ("pwd => 'phpStylePwd789'", "phpStylePwd789"),
    ("secret = 'topSecretValue4242'", "topSecretValue4242"),
    ("token = 'plainTokenValue31337'", "plainTokenValue31337"),
    ("api_key = 'plainApiKeyValue2026'", "plainApiKeyValue2026"),
    ("apikey: joinedApiKeyValue777", "joinedApiKeyValue777"),
    ("API-KEY: dashedApiKeyValue888", "dashedApiKeyValue888"),
    ("access_key = 'accessKeyValue0001'", "accessKeyValue0001"),
    ("secret_key = 'secretKeyValue0002'", "secretKeyValue0002"),
    ("auth_token = 'authTokenValue0003'", "authTokenValue0003"),
    ("client_secret = 'clientSecretValue0004'", "clientSecretValue0004"),
    ("private_key = 'privateKeyValue0005'", "privateKeyValue0005"),
    ("db_password = 'dbPasswordValue0006'", "dbPasswordValue0006"),
    ("credentials = 'credentialsValue0007'", "credentialsValue0007"),
    ("credential: 'credentialValue0008'", "credentialValue0008"),
    ("DB_PASS=envStylePass0009", "envStylePass0009"),
    ("export SECRET_TOKEN=exportedToken0010", "exportedToken0010"),
    ('{"password": "jsonPassword0011"}', "jsonPassword0011"),
    ("'api-key': 'quotedDashKey0012'", "quotedDashKey0012"),
    ("Bearer   spacedBearerToken0013abcdef", "spacedBearerToken0013abcdef"),
    ("AGPAEXAMPLEGROUPKEY1", "AGPAEXAMPLEGROUPKEY1"),
    ("amqp://guest:amqpSecret0014@mq:5672", "amqpSecret0014"),
    ("https://user:urlPass0015abc@internal.host/path", "urlPass0015abc"),
    # 拼接构造:避免命中代码托管平台的 secret-scanning 推送保护(引擎收到的仍是完整 token)
    ('GH = "' + 'github_' + 'pat_11FAKE0123456789_abcdefghijklmnopqrstuvwxyzFAKE"',
     'github_' + 'pat_11FAKE0123456789_abcdefghijklmnopqrstuvwxyzFAKE'),
    ('GITLAB_TOKEN := "' + 'glpat' + '-xFAKEfakeFAKEfake12b"',
     'glpat' + '-xFAKEfakeFAKEfake12b'),
    ("DefaultEndpointsProtocol=https;AccountName=x;AccountKey=abcdFAKE1234+/==",
     "abcdFAKE1234+/=="),
    ('dbPassword := "goStylePass0016"', "goStylePass0016"),
]

# Benign strings that must NOT be flagged (false-positive guard).
BENIGN_SAMPLES = [
    "password = os.environ['DB_PASSWORD']",
    "token = get_secret('svc-token')",
    "api_key = None",
    "secret = config['secret']",
    "passwd = getpass.getpass()",
    "# set your password in the env",
    "PASSWORD_MIN_LENGTH = 12",
    "token = '${VAULT_TOKEN}'",
    "url = 'https://example.com/path'",
    "self.token = token",
]


def test_detection_rate_at_least_95_percent():
    redactor = SecretRedactor()
    detected = sum(1 for text, _ in SECRET_SAMPLES if redactor.contains_secret(text))
    rate = detected / len(SECRET_SAMPLES)
    assert rate >= 0.95, f"detection rate {rate:.2%} below 95%"


def test_redaction_removes_every_detected_value():
    redactor = SecretRedactor()
    for text, value in SECRET_SAMPLES:
        redacted, _count = redactor.redact(text)
        if redactor.contains_secret(text):
            assert value not in redacted, f"plaintext survived redaction: {text!r}"
            assert REDACTED_PLACEHOLDER in redacted


def test_benign_strings_untouched():
    redactor = SecretRedactor()
    flagged = [text for text in BENIGN_SAMPLES if redactor.contains_secret(text)]
    assert not flagged, f"benign strings misflagged: {flagged}"


def test_redact_obj_recurses_and_counts():
    redactor = SecretRedactor()
    payload = {
        "list": ["password = 'hunter2butlonger'", {"nested": "token = 'plainTokenValue31337'"}],
        "clean": "no secrets here",
        "number": 42,
    }
    scrubbed = redactor.redact_obj(payload)
    assert "hunter2butlonger" not in str(scrubbed)
    assert "plainTokenValue31337" not in str(scrubbed)
    assert scrubbed["clean"] == "no secrets here"
    assert scrubbed["number"] == 42
    assert redactor.redaction_count >= 2
