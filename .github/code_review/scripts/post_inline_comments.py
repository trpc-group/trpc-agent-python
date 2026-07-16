#!/usr/bin/env python3

import json
import os
import re
import sys
import urllib.error
import urllib.request


def read_findings(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("findings", [])


def build_position_map(diff_path):
    with open(diff_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    position_map = {}
    current_path = None
    new_line_no = None
    position = 0
    in_hunk = False

    for raw in lines:
        line = raw.rstrip("\n")

        if line.startswith("diff --git "):
            current_path = None
            new_line_no = None
            position = 0
            in_hunk = False
            continue

        if line.startswith("+++ b/"):
            current_path = line[6:]
            if current_path not in position_map:
                position_map[current_path] = {}
            continue

        if line.startswith("@@ "):
            match = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if match:
                new_line_no = int(match.group(1))
                in_hunk = True
            continue

        if not in_hunk or current_path is None:
            continue

        if not line:
            continue

        prefix = line[0]
        if prefix not in (" ", "+", "-"):
            continue

        position += 1

        if prefix == " ":
            position_map[current_path][new_line_no] = position
            new_line_no += 1
        elif prefix == "+":
            position_map[current_path][new_line_no] = position
            new_line_no += 1
        elif prefix == "-":
            continue

    return position_map


def build_payload(commit_id, path, position, title, body):
    comment_body = "**%s**" % title
    if body:
        comment_body += "\n\n%s" % body
    return {
        "body": comment_body,
        "commit_id": commit_id,
        "path": path,
        "position": position,
    }


def post_inline_comment(api_url, token, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        api_url,
        data=data,
        method="POST",
        headers={
            "Authorization": "Bearer %s" % token,
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return True, "status=%s" % resp.status
    except urllib.error.HTTPError as e:
        try:
            err = e.read().decode("utf-8", errors="ignore")
        except Exception:
            err = ""
        return False, "status=%s body=%s" % (e.code, err)


def main():
    findings_path = os.environ.get("FINDINGS_PATH", "findings.json")
    diff_path = os.environ.get("PR_DIFF_PATH", "pr.diff")
    api_url = os.environ["INLINE_COMMENT_API_URL"]
    token = os.environ["GITHUB_TOKEN"]
    commit_id = os.environ["GITHUB_HEAD_SHA"]

    findings = read_findings(findings_path)
    if not findings:
        print("no findings found, skip inline comments")
        return 0

    position_map = build_position_map(diff_path)
    created = 0

    for finding in findings:
        if finding.get("severity") != "critical":
            continue
        if not finding.get("inline_candidate"):
            continue

        path = finding.get("path")
        start_line = finding.get("start_line")
        end_line = finding.get("end_line")
        title = finding.get("title", "").strip()
        body = finding.get("body", "").strip()

        if not path or not start_line or not title:
            print("skip finding without required fields: %s" % json.dumps(finding, ensure_ascii=False))
            continue

        start_line = int(start_line)
        end_line = int(end_line) if end_line else None
        target_line = start_line

        file_positions = position_map.get(path, {})
        position = file_positions.get(target_line)
        if position is None and end_line and end_line > start_line:
            position = file_positions.get(end_line)
            if position is not None:
                target_line = end_line

        target = "%s:%s-%s" % (path, start_line, end_line) if end_line and end_line > start_line else "%s:%s" % (path, start_line)

        if position is None:
            print("skip finding without diff position: %s" % target)
            continue

        payload = build_payload(commit_id, path, position, title, body)
        ok, msg = post_inline_comment(api_url, token, payload)
        if ok:
            created += 1
            print("inline comment created: %s position=%s %s" % (target, position, msg))
        else:
            print("inline comment failed: %s position=%s %s" % (target, position, msg))

    print("inline comments created: %s" % created)
    return 0


if __name__ == "__main__":
    sys.exit(main())
