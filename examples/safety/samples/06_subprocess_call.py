"""Sample 06 — Subprocess call with a non-allowed command.

Expected decision: NEEDS_HUMAN_REVIEW
Triggers: PROC-001 (non-allowed command "curl")
"""

import subprocess


def download_file(url: str, output: str):
    """Download a file using curl subprocess — not in allowed commands list."""
    result = subprocess.run(
        ["curl", "-o", output, url],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Download failed: {result.stderr}")
    return output


if __name__ == "__main__":
    download_file("https://example.com/data.csv", "/tmp/data.csv")
    print("Download complete")
