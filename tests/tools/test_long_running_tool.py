from trpc_agent_sdk.tools import is_tool_execution_error


def test_provider_argument_parse_error_is_not_a_long_running_success():
    assert is_tool_execution_error(
        {
            "result": "An error occurred while parsing tool arguments. Please try again with valid JSON.",
        }
    )


def test_normal_tool_result_is_not_classified_as_an_invocation_error():
    assert not is_tool_execution_error({"status": "pending", "questions": []})
    assert not is_tool_execution_error({"status": "success", "value": True})


def test_explicit_tool_errors_are_classified():
    assert is_tool_execution_error({"status": "error", "error_message": "bad input"})
    assert is_tool_execution_error({"error": "validation failed"})
