"""Minimal OpenAI/vLLM-compatible mock server for local development.

Lets the whole stack (LLM client, reasoning parser, tool-call protocol,
context counters) be exercised end-to-end without a GPU or a real RunPod
endpoint. Behaviour is selected purely by suffixes on the requested
`model` id, so the same server can emulate every vLLM configuration the
reasoning/tool-call layer needs to handle:

  <base>              reasoning via `reasoning_content` delta field,
                      native `tool_calls` when `tools` is present.
  <base>-inline-think reasoning emitted as literal <think>...</think>
                      tags inside `content`, no `reasoning_content` field.
  <base>-noreasoning  never emits any reasoning output.
  <base>-notools      ignores the `tools` param, never emits tool_calls
                      (forces the client's json_protocol fallback).

Reasoning output is driven by BOTH the model suffix and the request's
`reasoning_effort` field, so the full path - published model id -> level
-> upstream field -> parsed response - can be exercised end to end. An
effort of "none" (or an absent field) means no reasoning, matching how a
real endpoint treats the "off" level.

Whenever a request's messages contain the literal "```plan" marker (the
planner's own instruction text asks for a response fenced that way), a
canned plan JSON block is returned as content instead of the generic
`CONTENT_TEXT`, so `services.agent.planner.generate_plan()` can be
exercised end-to-end against this server rather than only unit-tested
against a hand-rolled fake client. Likewise for "```tool_call" (the
json_protocol system prompt's own marker, from
`services.llm.toolcalls.build_json_protocol_system_prompt`): returns a
canned `finish_step` call in that format, for exercising the
json_protocol fallback path through `services.agent.executor` end to end.

Run directly: `python mock_vllm.py` (defaults to 0.0.0.0:8000).
"""

import json
import time
import uuid
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

app = FastAPI(title="mock-vllm")

REASONING_TEXT = "Analyzing the request and considering the available context before answering."
CONTENT_TEXT = "This is a mock completion used for local development against the LLM-Hell agent loop."

CANNED_PLAN = json.dumps(
    [
        {
            "id": "step-1",
            "title": "Investigate the failing behaviour",
            "intent": "Find the root cause before changing anything.",
            "files": ["src/main.py"],
            "done_when": "The root cause is identified and written down.",
        },
        {
            "id": "step-2",
            "title": "Apply the fix",
            "intent": "Correct the implementation.",
            "files": ["src/main.py"],
            "done_when": "The tests pass.",
        },
    ]
)
PLAN_RESPONSE_TEXT = f"```plan\n{CANNED_PLAN}\n```"

CANNED_TOOL_CALL = json.dumps({"tool": "finish_step", "arguments": {"status": "done", "summary": "Mock finished the step."}})
TOOL_CALL_RESPONSE_TEXT = f"```tool_call\n{CANNED_TOOL_CALL}\n```"


def _wants_plan(body: dict[str, Any]) -> bool:
    return any("```plan" in (m.get("content") or "") for m in body.get("messages", []))


def _wants_json_protocol_tool_call(body: dict[str, Any]) -> bool:
    return any("```tool_call" in (m.get("content") or "") for m in body.get("messages", []))


def _pick_content_text(body: dict[str, Any]) -> str:
    if _wants_plan(body):
        return PLAN_RESPONSE_TEXT
    if _wants_json_protocol_tool_call(body):
        return TOOL_CALL_RESPONSE_TEXT
    return CONTENT_TEXT


def _chunk(id_: str, model: str, delta: dict[str, Any], finish_reason: str | None = None) -> str:
    payload = {
        "id": id_,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(payload)}\n\n"


def _usage_chunk(id_: str, model: str, prompt_tokens: int, completion_tokens: int) -> str:
    """The client always sends `stream_options: {include_usage: true}`, so
    a real vLLM/OpenAI server appends one extra chunk after the
    finish_reason chunk with empty `choices` and a populated `usage`
    field - mirror that here so `services.llm.client`'s usage handling
    (and anything built on top of it, like run-level token/cost
    accounting) has something to see in tests and local dev, not just
    against a real endpoint.
    """
    payload = {
        "id": id_,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }
    return f"data: {json.dumps(payload)}\n\n"


def _mock_tool_call(tools: list[dict[str, Any]]) -> dict[str, Any]:
    first = tools[0]["function"]
    name = first["name"]
    args: dict[str, Any] = {}
    if name in ("list_dir", "read_file", "grep"):
        args = {"path": "."}
    elif name == "write_file":
        args = {"path": "mock_output.txt", "content": "mock content"}
    elif name in ("run_command", "run_tests"):
        args = {"command": "echo mock"}
    elif name == "finish_step":
        args = {"status": "done", "summary": "Mock finished the step."}
    return {
        "id": f"call_{uuid.uuid4().hex[:8]}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


async def _stream_completion(body: dict[str, Any]):
    model: str = body.get("model", "mock")
    tools = body.get("tools")

    inline_think = "-inline-think" in model
    no_reasoning = "-noreasoning" in model
    # "none" and an absent field both mean "don't reason" - the proxy sends
    # reasoning_effort=none for its "off" level rather than omitting it.
    reasoning_effort = (body.get("reasoning_effort") or "none").lower()
    reasoning_requested = reasoning_effort != "none"
    no_tools = "-notools" in model

    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    completion_text = ""

    yield _chunk(completion_id, model, {"role": "assistant"})

    if reasoning_requested and not no_reasoning:
        if inline_think:
            yield _chunk(completion_id, model, {"content": "<think>"})
            for word in REASONING_TEXT.split():
                yield _chunk(completion_id, model, {"content": word + " "})
            yield _chunk(completion_id, model, {"content": "</think>"})
            completion_text += f"<think>{REASONING_TEXT}</think>"
        else:
            for word in REASONING_TEXT.split():
                yield _chunk(completion_id, model, {"reasoning_content": word + " "})
            completion_text += REASONING_TEXT

    if tools and not no_tools:
        tool_call = _mock_tool_call(tools)
        yield _chunk(completion_id, model, {"tool_calls": [{"index": 0, **tool_call}]})
        yield _chunk(completion_id, model, {}, finish_reason="tool_calls")
        completion_text += json.dumps(tool_call)
    else:
        content_text = _pick_content_text(body)
        for word in content_text.split(" "):
            yield _chunk(completion_id, model, {"content": word + " "})
        yield _chunk(completion_id, model, {}, finish_reason="stop")
        completion_text += content_text

    prompt_tokens = _estimate_tokens(json.dumps(body.get("messages", [])))
    yield _usage_chunk(completion_id, model, prompt_tokens, _estimate_tokens(completion_text))
    yield "data: [DONE]\n\n"


def _full_completion(body: dict[str, Any]) -> dict[str, Any]:
    model: str = body.get("model", "mock")
    tools = body.get("tools")

    inline_think = "-inline-think" in model
    no_reasoning = "-noreasoning" in model
    # "none" and an absent field both mean "don't reason" - the proxy sends
    # reasoning_effort=none for its "off" level rather than omitting it.
    reasoning_effort = (body.get("reasoning_effort") or "none").lower()
    reasoning_requested = reasoning_effort != "none"
    no_tools = "-notools" in model

    message: dict[str, Any] = {"role": "assistant", "content": ""}
    finish_reason = "stop"

    if reasoning_requested and not no_reasoning:
        if inline_think:
            message["content"] = f"<think>{REASONING_TEXT}</think>"
        else:
            message["reasoning_content"] = REASONING_TEXT

    if tools and not no_tools:
        message["tool_calls"] = [_mock_tool_call(tools)]
        # Real APIs still surface inline reasoning tags ahead of a tool call;
        # only null out content when there was nothing to say.
        if not message.get("content"):
            message["content"] = None
        finish_reason = "tool_calls"
    else:
        content_text = _pick_content_text(body)
        message["content"] = (message.get("content") or "") + content_text

    prompt_tokens = _estimate_tokens(json.dumps(body.get("messages", [])))
    completion_tokens = _estimate_tokens(str(message))

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    if body.get("stream"):
        return StreamingResponse(_stream_completion(body), media_type="text/event-stream")
    return _full_completion(body)


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


@app.post("/tokenize")
async def tokenize(request: Request):
    body = await request.json()
    text = body.get("prompt") or body.get("text") or ""
    count = _estimate_tokens(text)
    return {"tokens": list(range(count)), "count": count}


@app.get("/v1/models")
async def models():
    return {
        "object": "list",
        "data": [
            {"id": "glm-4.7", "object": "model"},
            {"id": "glm-4.7-flash", "object": "model"},
        ],
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
