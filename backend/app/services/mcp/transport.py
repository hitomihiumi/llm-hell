"""The only module in this application that imports the MCP SDK.

Everything else - connectors, the registry, the search service - works with
`RawToolResult`, a plain dataclass. That containment is deliberate: the SDK
is on a fast-moving 1.x line, and 2.0 already swaps httpx for httpx2 and
reshapes the client API. When that migration happens it is this file plus
the pin in requirements.txt, not a rewrite of every connector.

Three transports, because the three servers genuinely differ:

  * `http_session`  - streamable HTTP (GitLab, and the Google bridge)
  * `sse_session`   - SSE (postgres-mcp offers nothing else; see
                      docs/mcp-spike-findings.md)
  * `stdio_session` - spawns the server as a child process (the Google
                      Workspace server natively, when not bridged)
"""

import ast
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

logger = logging.getLogger("llmhell.mcp")

# How much of an unrecognised payload to put in the log. Enough to identify
# the shape, bounded so a runaway response cannot fill the disk.
_LOG_PAYLOAD_CHARS = 2000


class McpError(Exception):
    """Anything that went wrong talking to an MCP server."""


class McpToolError(McpError):
    """The server ran the tool and reported failure."""


@dataclass
class RawToolResult:
    """One tool call's response, normalised away from SDK types.

    `structured` is whatever the server put in `structuredContent`, which in
    practice is very often None - postgres-mcp never populates it at all.
    `text` is every text block concatenated. `blocks` keeps the originals so
    a connector can reach for an image or resource block if it ever needs to.
    """

    is_error: bool = False
    structured: Any = None
    text: str = ""
    blocks: list[dict[str, Any]] = field(default_factory=list)

    def payload(self, *, source: str = "?", tool: str = "?") -> Any:
        """The response body as Python data, or None if it cannot be read.

        A deliberate ladder, because MCP servers disagree about how to
        return data and none of them documents which way they chose:

          1. `structuredContent`, when present. The intended mechanism.
          2. JSON in the text block. Common.
          3. A Python `repr()` in the text block, via `ast.literal_eval`.
             postgres-mcp does this for every single tool.

        Returns None rather than raising, and logs once, because a source
        that returns an unreadable shape should contribute no results - not
        break the other three sources' search.

        NOTE `literal_eval` is not `eval`: it parses literals only and will
        not call anything. A repr containing a constructor call - which
        postgres-mcp produces for timestamp columns - fails here on purpose.
        See `app/services/mcp/postgres.py` for how generated SQL is wrapped
        so that never arises.
        """
        if self.structured is not None:
            return self.structured
        if not self.text:
            return None

        try:
            return json.loads(self.text)
        except (json.JSONDecodeError, ValueError):
            pass

        try:
            return ast.literal_eval(self.text)
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            logger.warning(
                "%s.%s returned an unreadable payload (neither JSON nor a Python literal): %.*s",
                source,
                tool,
                _LOG_PAYLOAD_CHARS,
                self.text,
            )
            return None


def summarise_exception(exc: BaseException, *, depth: int = 0) -> str:
    """A one-line description, flattening ExceptionGroups.

    anyio task groups - which every MCP transport opens - re-raise whatever
    happened inside as a `BaseExceptionGroup`, whose own str() is the
    famously unhelpful "unhandled errors in a TaskGroup (1 sub-exception)".
    That string ends up in `SourceResult.error` and then on the screen next
    to the source's name, so it needs to say what actually went wrong.
    """
    if isinstance(exc, BaseExceptionGroup) and exc.exceptions and depth < 5:
        return "; ".join(summarise_exception(inner, depth=depth + 1) for inner in exc.exceptions[:3])
    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


def _block_to_dict(block: Any) -> dict[str, Any]:
    dump = getattr(block, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json")
        except TypeError:
            return dump()
    return {"type": getattr(block, "type", "unknown"), "repr": repr(block)}


@asynccontextmanager
async def http_session(url: str, headers: dict[str, str] | None = None) -> AsyncIterator[ClientSession]:
    async with streamablehttp_client(url, headers=headers or None) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


@asynccontextmanager
async def sse_session(url: str, headers: dict[str, str] | None = None) -> AsyncIterator[ClientSession]:
    # Note the 2-tuple: sse_client yields (read, write) where
    # streamablehttp_client yields (read, write, get_session_id).
    async with sse_client(url, headers=headers or None) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


@asynccontextmanager
async def stdio_session(
    command: str, args: list[str], env: dict[str, str] | None = None
) -> AsyncIterator[ClientSession]:
    """Spawn an MCP server as a child process.

    IMPORTANT: `stdio_client` opens an anyio task group, so the `async with`
    must be entered and exited **in the same task**. Handing the yielded
    session to another task and closing it there raises a cancel-scope
    error. `registry.py` keeps a single supervisor task for exactly this
    reason.
    """
    params = StdioServerParameters(command=command, args=args, env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def list_tool_names(session: ClientSession, *, timeout: float = 30.0) -> list[str]:
    """Every tool the server offers, following pagination.

    GitLab's server exposes ~65 tools and pages them; stopping at the first
    page would hide most of them, including the search tools we care about.
    """
    names: list[str] = []
    cursor: str | None = None
    while True:
        page = await asyncio.wait_for(session.list_tools(cursor=cursor), timeout=timeout)
        names.extend(tool.name for tool in page.tools)
        cursor = getattr(page, "nextCursor", None)
        if not cursor:
            break
    return names


async def call_tool(
    session: ClientSession,
    name: str,
    arguments: dict[str, Any],
    *,
    timeout: float,
    text_error_prefixes: tuple[str, ...] = (),
) -> RawToolResult:
    """Call one tool and normalise the result. Raises `McpToolError` if the
    server reported failure, `McpError` on timeout.

    `text_error_prefixes` exists because **`isError` is not reliable**.
    postgres-mcp in restricted mode refuses a statement by returning a
    perfectly successful result whose text begins with "Error: " - a caller
    checking only the flag would parse the refusal message as data.

    It defaults to empty rather than to `("Error:",)` on purpose: a *search*
    result can legitimately contain text starting with "Error:" - searching
    a codebase for that string is an obvious thing to do - so this must be
    opted into per server, by a connector that knows the convention.
    """
    try:
        result = await asyncio.wait_for(session.call_tool(name, arguments), timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise McpError(f"{name} timed out after {timeout}s") from exc
    except (McpError, McpToolError):
        raise
    except Exception as exc:  # noqa: BLE001
        # The SDK raises its OWN mcp.shared.exceptions.McpError - a
        # different class from the one defined in this module - for a
        # protocol-level failure, such as a tool rejecting its arguments or
        # the upstream API returning 4xx. Letting that escape defeats the
        # entire point of this file: callers catch the local McpError, miss
        # it, and the exception then crosses an anyio task group and arrives
        # as an unreadable "unhandled errors in a TaskGroup".
        raise McpToolError(f"{name} failed: {summarise_exception(exc)}") from exc

    blocks = [_block_to_dict(block) for block in (result.content or [])]
    text = "".join(block.get("text", "") for block in blocks if block.get("type") == "text")

    raw = RawToolResult(
        is_error=bool(getattr(result, "isError", False)),
        structured=getattr(result, "structuredContent", None),
        text=text,
        blocks=blocks,
    )

    if raw.is_error:
        raise McpToolError(f"{name} failed: {text[:500] or '(no message)'}")

    stripped = text.lstrip()
    for prefix in text_error_prefixes:
        if stripped.startswith(prefix):
            raise McpToolError(f"{name} failed: {stripped[:500]}")

    return raw
