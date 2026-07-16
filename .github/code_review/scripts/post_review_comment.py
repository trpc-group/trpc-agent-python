#!/usr/bin/env python3

import json
import os
import re
import sys
import urllib.error
import urllib.request


def load_review(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def add_github_links(body, repo_url, head_sha):
    pattern = re.compile(r"`((?:\.?[\w.-]+/)*[\w.-]+\.[\w.+-]+):(\d+)(?:-(\d+))?`")

    def repl(match):
        file_path = match.group(1)
        start = match.group(2)
        end = match.group(3)
        label = "%s:%s%s" % (file_path, start, ("-%s" % end) if end else "")
        anchor = "#L%s%s" % (start, ("-L%s" % end) if end else "")
        url = "%s/blob/%s/%s%s" % (repo_url, head_sha, file_path, anchor)
        return "[`%s`](%s)" % (label, url)

    return pattern.sub(repl, body)


def post_comment(api_url, token, body):
    payload = json.dumps({"body": body}).encode("utf-8")
    req = urllib.request.Request(
        api_url,
        data=payload,
        method="POST",
        headers={
            "Authorization": "Bearer %s" % token,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="ignore")


def main():
    review_path = os.environ.get("REVIEW_PATH", "review.md")
    api_url = os.environ["COMMENT_API_URL"]
    repo_url = os.environ["GITHUB_WEB_REPO_URL"].rstrip("/")
    head_sha = os.environ["GITHUB_HEAD_SHA"]
    token = os.environ["GITHUB_TOKEN"]

    body = load_review(review_path)
    if not body:
        body = "AI Code Review 未生成有效结果，请检查流水线日志。"

    body = add_github_links(body, repo_url, head_sha)
    status, response = post_comment(api_url, token, "## AI Code Review\n\n%s" % body)

    print("comment http code: %s" % status)
    if status < 200 or status >= 300:
        print("failed to create github comment")
        print(response)
        return 1

    print("comment created successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
