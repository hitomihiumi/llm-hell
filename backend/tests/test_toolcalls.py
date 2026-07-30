from app.services.llm.toolcalls import (
    ToolSpec,
    build_json_protocol_system_prompt,
    build_native_tools,
    parse_json_protocol_response,
)

READ_FILE_TOOL = ToolSpec(
    name="read_file",
    description="Read a file from the workspace.",
    parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
)


def test_build_native_tools_shape() -> None:
    tools = build_native_tools([READ_FILE_TOOL])
    assert tools == [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file from the workspace.",
                "parameters": READ_FILE_TOOL.parameters,
            },
        }
    ]


def test_json_protocol_prompt_mentions_tool_name_and_schema() -> None:
    prompt = build_json_protocol_system_prompt([READ_FILE_TOOL])
    assert "read_file" in prompt
    assert "```tool_call" in prompt
    assert '"path"' in prompt


def test_parse_json_protocol_response_from_fenced_block() -> None:
    content = 'Sure, calling a tool:\n```tool_call\n{"tool": "read_file", "arguments": {"path": "a.py"}}\n```'
    call = parse_json_protocol_response(content)
    assert call is not None
    assert call.name == "read_file"
    assert call.arguments == {"path": "a.py"}


def test_parse_json_protocol_response_bare_json() -> None:
    content = '{"tool": "read_file", "arguments": {"path": "b.py"}}'
    call = parse_json_protocol_response(content)
    assert call is not None
    assert call.name == "read_file"


def test_parse_json_protocol_response_returns_none_for_plain_text() -> None:
    assert parse_json_protocol_response("Just a normal answer, no tool call here.") is None


def test_parse_json_protocol_response_returns_none_for_empty() -> None:
    assert parse_json_protocol_response("") is None
