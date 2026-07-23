from trpc_agent_sdk.events import Event
from trpc_agent_sdk.tools import is_tool_execution_error


def test_framework_argument_error_is_not_a_long_running_success():
    assert is_tool_execution_error(Event(error_code="tool_argument_error"))


def test_business_tool_errors_are_not_classified_as_invocation_errors():
    assert not is_tool_execution_error(Event())
    assert not is_tool_execution_error(Event(content=None, custom_metadata={"result": "json parse error"}))


def test_framework_execution_errors_are_classified():
    assert is_tool_execution_error(Event(error_code="tool_execution_error"))
    assert is_tool_execution_error(Event(error_code="tool_not_found"))
