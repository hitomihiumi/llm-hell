#!/usr/bin/env python
"""Connect to an MCP server, call a tool, and print exactly what comes back.

**Why this exists.** None of the three MCP servers this project talks to
documents the *shape* of its tool responses - the READMEs list tool names
and arguments, and stop there. Writing a result adapter against a guess is
how you get a connector that silently returns nothing in production, so the
rule for this repo is: capture a real response first, then write the
adapter against it as a pure function.

Deliberately standalone. It imports no application module, needs no
database, and does not read `.env` through Settings - so it works before any
of that exists, and it works when the app itself is broken. That is the
whole point of a probe.

    # what tools does this server actually have?
    python tools/mcp_probe.py --url http://localhost:8002/mcp list

    # what does a result look like?
    python tools/mcp_probe.py --url http://localhost:8002/mcp \
        call list_schemas '{}'

    # capture it as a test fixture
    python tools/mcp_probe.py --url http://localhost:3102/mcp \
        --header "Authorization: Bearer glpat-xxx" \
        call search_code '{"search": "auth"}' \
        --save backend/tests/fixtures/mcp/gitlab_search_code.json

    # stdio servers are spawned rather than connected to
    python tools/mcp_probe.py --stdio "npx -y @aaronsb/google-workspace-mcp" \
        --env GOOGLE_CLIENT_ID=... --env GOOGLE_CLIENT_SECRET=... list

Requires only `pip install mcp`.
"""

import argparse
import asyncio
import json
import os
import shlex
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.sse import sse_client
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamablehttp_client
except ImportError:  # pragma: no cover - operator-facing message
    print("The MCP SDK is missing. Install it with:  pip install mcp", file=sys.stderr)
    raise SystemExit(1)


@asynccontextmanager
async def open_session(args: argparse.Namespace) -> AsyncIterator[ClientSession]:
    if args.stdio:
        command, *command_args = shlex.split(args.stdio)
        # Inherit the ambient environment: these servers look up binaries on
        # PATH (the Google one shells out to `gws`) and read HOME to find
        # their credential store.
        env = dict(os.environ)
        env.update(dict(pair.split("=", 1) for pair in args.env))
        params = StdioServerParameters(command=command, args=command_args, env=env)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
        return

    headers = {}
    for raw in args.header:
        name, _, value = raw.partition(":")
        headers[name.strip()] = value.strip()

    if args.sse:
        # sse_client yields a 2-tuple; streamablehttp_client yields a
        # 3-tuple (the third is a session-id getter).
        async with sse_client(args.sse, headers=headers or None) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session
        return

    async with streamablehttp_client(args.url, headers=headers or None) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


def _block_to_dict(block: Any) -> dict[str, Any]:
    """Content blocks are pydantic models whose exact class varies by type
    and by SDK version. Dump whatever it is rather than assuming."""
    for attr in ("model_dump", "dict"):
        dump = getattr(block, attr, None)
        if callable(dump):
            try:
                return dump(mode="json") if attr == "model_dump" else dump()
            except TypeError:
                return dump()
    return {"repr": repr(block)}


def _maybe_json(text: str) -> Any:
    """Servers routinely return JSON as a *text* block rather than as
    structured content, so a captured fixture is far more useful with that
    layer already peeled off - but only when it really is JSON."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


async def cmd_list(session: ClientSession, args: argparse.Namespace) -> dict[str, Any]:
    tools: list[dict[str, Any]] = []
    cursor = None
    # Paginated. A server with many tools (GitLab has ~65) returns them in
    # pages, and stopping at the first one silently hides the rest.
    while True:
        page = await session.list_tools(cursor=cursor) if cursor else await session.list_tools()
        for tool in page.tools:
            tools.append(
                {
                    "name": tool.name,
                    "description": (tool.description or "").strip(),
                    "input_schema": tool.inputSchema,
                }
            )
        cursor = getattr(page, "nextCursor", None)
        if not cursor:
            break

    print(f"{len(tools)} tool(s):\n")
    for tool in tools:
        summary = tool["description"].splitlines()[0] if tool["description"] else ""
        print(f"  {tool['name']:<32} {summary[:90]}")
        if args.schemas:
            print(f"      args: {json.dumps(tool['input_schema'])[:400]}")
    return {"tools": tools}


async def cmd_call(session: ClientSession, args: argparse.Namespace) -> dict[str, Any]:
    try:
        arguments = json.loads(args.arguments)
    except json.JSONDecodeError as exc:
        print(f"arguments must be a JSON object: {exc}", file=sys.stderr)
        raise SystemExit(2)

    result = await session.call_tool(args.tool, arguments)

    blocks = [_block_to_dict(block) for block in (result.content or [])]
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")

    captured = {
        "tool": args.tool,
        "arguments": arguments,
        # `is_error` is a FLAG, not an exception - a failed tool call still
        # returns a normal result object, and code that does not check this
        # will happily parse an error message as data.
        "is_error": bool(getattr(result, "isError", False)),
        "structured_content": getattr(result, "structuredContent", None),
        "content_blocks": blocks,
        "text_as_json": _maybe_json(text),
    }

    print(f"is_error: {captured['is_error']}")
    print(f"structured_content: {json.dumps(captured['structured_content'], indent=2, default=str)[:4000]}")
    print(f"\n{len(blocks)} content block(s):")
    for block in blocks:
        print(f"  type={block.get('type')} {json.dumps(block, default=str)[:2000]}")
    if captured["text_as_json"] is not None:
        print(f"\ntext block parsed as JSON:\n{json.dumps(captured['text_as_json'], indent=2)[:4000]}")
    return captured


async def main_async(args: argparse.Namespace) -> None:
    async with open_session(args) as session:
        captured = await (cmd_list(session, args) if args.command == "list" else cmd_call(session, args))

    if args.save:
        path = Path(args.save)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(captured, indent=2, default=str), encoding="utf-8")
        print(f"\nsaved to {path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mcp_probe.py", description=__doc__.splitlines()[0])
    parser.add_argument("--url", help="Streamable-HTTP endpoint, e.g. http://localhost:3102/mcp")
    parser.add_argument("--sse", help="SSE endpoint, e.g. http://localhost:8002/sse")
    parser.add_argument("--stdio", help="Command to spawn instead, e.g. 'npx -y @scope/server'")
    parser.add_argument("--header", action="append", default=[], help='Repeatable: "Name: value"')
    parser.add_argument("--env", action="append", default=[], help="Repeatable: NAME=value (stdio only)")

    # --save lives on a shared parent rather than on the main parser, so it
    # can be written AFTER the subcommand - which is where anyone would
    # naturally put it, and where the examples above have it. argparse only
    # accepts main-parser options before the subcommand name.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--save", help="Write the captured response to this path as JSON")

    subparsers = parser.add_subparsers(dest="command", required=True)

    p = subparsers.add_parser("list", parents=[common], help="List the server's tools")
    p.add_argument("--schemas", action="store_true", help="Also print each tool's input schema")

    p = subparsers.add_parser("call", parents=[common], help="Call one tool and dump the raw result")
    p.add_argument("tool")
    p.add_argument("arguments", nargs="?", default="{}", help="JSON object, defaults to {}")

    return parser


def main() -> None:
    args = build_parser().parse_args()
    chosen = [name for name in ("url", "sse", "stdio") if getattr(args, name)]
    if len(chosen) != 1:
        print("exactly one of --url, --sse or --stdio is required", file=sys.stderr)
        raise SystemExit(2)
    # `schemas` only exists on the list subparser; give call a default so
    # cmd_list/cmd_call can share one Namespace.
    if not hasattr(args, "schemas"):
        args.schemas = False
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
