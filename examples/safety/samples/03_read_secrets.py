"""Sample 03 — Hardcoded AWS credentials in source code.

Expected decision: DENY
Triggers: SEC-001 (hardcoded secrets — AWS key pattern)
"""

import boto3

# Hardcoded AWS credentials — NEVER do this in production!
AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE"
AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"


def get_s3_client():
    """Create an S3 client with hardcoded credentials."""
    return boto3.client(
        "s3",
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name="us-east-1",
    )


if __name__ == "__main__":
    client = get_s3_client()
    print("Connected to S3")
