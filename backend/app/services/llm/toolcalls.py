"""Tool-call protocol: native OpenAI `tools` API vs. a JSON-in-content
fallback for endpoints where tool-call support is unknown or absent.

`tools_mode` on the endpoint picks which of these the agent loop (task 6)
uses. Native mode passes `build_native_tools()` output as the request's
`tools` field and relies on the server returning structured `tool_calls`.
JSON-protocol mode instead injects a system-prompt block describing the
tools and asks the model to answer with a fenced ```tool_call block; the
agent loop parses that back out with `parse_json_protocol_response`.
"""

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema for the arguments object


@dataclass(frozen=True)
class ToolCallRequest:
    name: str
    arguments: dict[str, Any]
    call_id: str | None = None


def build_native_tools(tool_specs: list[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            },
        }
        for spec in tool_specs
    ]


_JSON_PROTOCOL_BLOCK_RE = re.compile(r"```tool_call\s*(\{.*?\})\s*```", re.DOTALL)


def build_json_protocol_system_prompt(tool_specs: list[ToolSpec]) -> str:
    tool_docs = "\n\n".join(
        f"### {spec.name}\n{spec.description}\nArguments JSON schema:\n"
        f"```json\n{json.dumps(spec.parameters, ensure_ascii=False, indent=2)}\n```"
        for spec in tool_specs
    )
    return (
        "You can call the following tools. To call one, respond with ONLY a single "
        "fenced code block, no other text, in this exact form:\n\n"
        "```tool_call\n"
        '{"tool": "<tool name>", "arguments": {<arguments matching the schema>}}\n'
        "```\n\n"
        "If you are not calling a tool, answer normally in plain text instead.\n\n"
        f"Available tools:\n\n{tool_docs}"
    )


def parse_json_protocol_response(content: str) -> ToolCallRequest | None:
    if not content:
        return None

    match = _JSON_PROTOCOL_BLOCK_RE.search(content)
    raw = match.group(1) if match else content.strip()

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict) or "tool" not in payload:
        return None

    return ToolCallRequest(name=payload["tool"], arguments=payload.get("arguments") or {})
