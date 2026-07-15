import os
import subprocess
def test_bad_code_example():
    user_input = "../../etc/passwd"
    # Intentionally bad code for AI review testing.
    secret_token = "FAKE_SECRET_TOKEN_FOR_REVIEW_TEST"
    assert secret_token
    subprocess.run(f"cat {user_input}", shell=True)
    try:
        int("not-a-number")
    except Exception:
        pass
    assert True