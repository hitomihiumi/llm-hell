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
import re
import time
import uuid
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

app = FastAPI(title="mock-vllm")

REASONING_TEXT = "Analyzing the request and considering the available context before answering."
# Deliberately Markdown, with citations. A real model answers in Markdown -
# headings, lists, bold, code, sometimes a table - so a plain-text stand-in
# cannot exercise the answer renderer at all, and the first time anyone points
# this at a live pod the formatting would be the thing that broke. `[1]`/`[2]`
# resolve against whatever hits were packed into the prompt; `[9]` is here on
# purpose to prove an out-of-range citation stays inert text.
CONTENT_TEXT = """Results are merged with **reciprocal rank fusion**, which deliberately ignores
each backend's own relevance score [1].

## Why the scores are not comparable

- GitLab returns no score at all
- Postgres returns whatever the generated `ORDER BY` produced
- Drive returns Google's own opaque ordering [2]

The only signal that means the same thing everywhere is *position within a
source's own results*, so each hit contributes:

```python
score = source_weight / (60 + rank_within_source)
```

| Source | Excerpt | Lands on the match |
| --- | --- | --- |
| GitLab | Matched lines | Yes |
| Postgres | Window around the match | Row, then passage |

> A citation the model invents, like [9], stays plain text - it resolves to no
> hit that was in the prompt.

This is a mock completion used for local development against the LLM-Hell
agent loop."""

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


# --- text2sql -------------------------------------------------------------
#
# Without this the mock answers every prompt with prose, so `parse_generated`
# rejects it and the Postgres source silently runs its deterministic
# fallback - which searches only the first configured table. The seeded
# corpus has four, so three of them were unreachable in the dev stack and
# the UI could never show `mode: llm` at all. This is the one prompt in the
# service whose reply has to be machine-readable, so it is the one the mock
# has to actually understand.

# Matches the system prompt in services/search/text2sql.py. Kept as a phrase
# rather than a sentinel because it is the instruction itself: if that
# wording is rewritten, this should stop matching and be updated with it.
_SQL_MARKER = "ONE PostgreSQL SELECT statement"

# Rendered by the connector as `table(col type, col type)`.
_SCHEMA_LINE = re.compile(r"^(\w+)\(([^)]*)\)$", re.MULTILINE)

# Which table a question is about. First hit wins, so the order is the
# priority order; anything unmatched falls through to the first table.
_TABLE_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tickets", ("error", "fail", "broken", "bug", "crash", "hang", "wrong", "ticket", "incident")),
    ("runbooks", ("how do i", "steps", "runbook", "restart", "rotate", "recover", "bring up", "verify")),
    ("decisions", ("decision", "why did we", "instead of", "rejected", "considered", "chose", "trade-off")),
)

_STOPWORDS = frozenset(
    "the a an and or but how why what when does do is are was were of for to in on at "
    "with from that this it its we our you your can could should would if then than".split()
)

_TIMESTAMPS = ("updated_at", "created_at", "decided_at", "occurred_at")
_AUTHORS = ("author", "reporter", "owner", "decided_by")


def _wants_sql(body: dict[str, Any]) -> bool:
    return any(_SQL_MARKER in (m.get("content") or "") for m in body.get("messages", []))


def _schema_from_prompt(body: dict[str, Any]) -> dict[str, list[str]]:
    system = "\n".join(m.get("content") or "" for m in body.get("messages", []) if m.get("role") == "system")
    return {
        match.group(1): [column.strip().split(" ")[0] for column in match.group(2).split(",") if column.strip()]
        for match in _SCHEMA_LINE.finditer(system)
    }


def _terms(question: str) -> list[str]:
    """The words worth matching on.

    A real model extracts keywords; searching for the whole question as one
    ILIKE pattern matches nothing, which would make the mock look like it
    works while returning an empty table every time.
    """
    words = [word for word in re.findall(r"[A-Za-z0-9_.-]{3,}", question.lower()) if word not in _STOPWORDS]
    # Longest first: the specific word in a question carries it.
    return sorted(dict.fromkeys(words), key=len, reverse=True)[:3] or [question.strip()[:40]]


def _sql_response(body: dict[str, Any]) -> str:
    schema = _schema_from_prompt(body)
    question = next(
        (m.get("content") or "" for m in reversed(body.get("messages", [])) if m.get("role") == "user"),
        "",
    )

    tables = list(schema) or ["articles"]
    lowered = question.lower()
    table = next(
        (name for name, hints in _TABLE_HINTS if name in tables and any(h in lowered for h in hints)),
        tables[0],
    )

    columns = schema.get(table, ["id", "title", "body"])
    timestamp = next((c for c in _TIMESTAMPS if c in columns), None)
    author = next((c for c in _AUTHORS if c in columns), None)
    snippet = "body" if "body" in columns else columns[-1]

    matched = ["title", snippet] if "title" in columns else [snippet]
    where = " OR ".join(
        f"{column} ILIKE '%{term.replace(chr(39), chr(39) * 2)}%'" for term in _terms(question) for column in matched
    )

    selected = ["id", "title", snippet] if "title" in columns else ["id", snippet]
    selected += [column for column in (timestamp, author) if column]
    order = f" ORDER BY {timestamp} DESC" if timestamp else ""

    return json.dumps(
        {
            "sql": f"SELECT {', '.join(dict.fromkeys(selected))} FROM {table} WHERE {where}{order} LIMIT 10",
            "table": table,
            "id_column": "id",
            "title_column": "title" if "title" in columns else snippet,
            "snippet_column": snippet,
            "timestamp_column": timestamp,
            "author_column": author,
        }
    )


# --- vision ---------------------------------------------------------------
#
# Stands in for the Qwen3-VL endpoint. Without this there is no way to test
# the PDF path end to end without a second GPU: the code that renders pages,
# calls a model and merges the result into the document is the part most
# likely to be wrong, and it is unreachable if nothing answers an image.
#
# The reply deliberately contains facts that exist ONLY in a diagram - pad
# names and a wiring order - so a test can prove the transcription reached the
# answer prompt rather than merely being computed and dropped.

VISION_TEXT = """\
Board top view, pad and connector layout.

UART pads, left edge, top to bottom: T1/R1, T2/R2, T3/R3, T4/R4, T6/R6.
UART3 (T3/R3) is the pad pair nearest the USB connector and is labelled
"GPS" in silkscreen.

Power: 5V and GND pads either side of the BEC block, marked 5V 2A.
The battery input pads are BAT+ and BAT-, bottom right, rated 3-6S.

Motor outputs S1-S4 run along the right edge, each with an adjacent GND.
A jumper marked JP1 selects between 5V and 9V on the VTX pad."""


def _wants_vision(body: dict[str, Any]) -> bool:
    """An OpenAI multimodal request: some message's content is a list with an
    image part in it, rather than a plain string."""
    for message in body.get("messages", []):
        content = message.get("content")
        if isinstance(content, list) and any(
            isinstance(part, dict) and part.get("type") == "image_url" for part in content
        ):
            return True
    return False


def _pick_content_text(body: dict[str, Any]) -> str:
    if _wants_plan(body):
        return PLAN_RESPONSE_TEXT
    if _wants_json_protocol_tool_call(body):
        return TOOL_CALL_RESPONSE_TEXT
    if _wants_vision(body):
        return VISION_TEXT
    if _wants_sql(body):
        return _sql_response(body)
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
