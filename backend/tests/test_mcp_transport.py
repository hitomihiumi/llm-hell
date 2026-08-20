"""Transport-level tests against a real MCP server, in-process.

`create_connected_server_and_client_session` wires a FastMCP server to a
real ClientSession over in-memory streams - no subprocess, no socket, but a
genuine protocol exchange. That makes it possible to test the things that
actually bite (is_error handling, timeouts, odd content shapes) without
standing up Docker.

This helper is 1.x-only; it is the single test that a move to the MCP SDK
2.0 line would have to be rewritten.
"""

import asyncio

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

from app.services.mcp.transport import (
    McpError,
    McpToolError,
    RawToolResult,
    call_tool,
    list_tool_names,
)


def build_server() -> FastMCP:
    server = FastMCP("test-server")

    # structured_output=False throughout, so these emulate the servers this
    # project actually talks to. FastMCP would otherwise helpfully wrap every
    # string return in structuredContent={"result": ...}, and the payload
    # ladder would never reach its text branches - which are the ones that
    # matter, because postgres-mcp leaves structuredContent null for every
    # single tool.

    @server.tool(structured_output=False)
    def echo_json(value: str) -> str:
        """Returns a JSON object as text, the way a well-behaved server does."""
        return '{"items": [{"id": 1, "title": "' + value + '"}]}'

    @server.tool(structured_output=False)
    def echo_repr() -> str:
        """Returns a Python repr, the way postgres-mcp does for every tool."""
        return "[{'id': 1, 'title': 'from repr', 'author': None}]"

    @server.tool(structured_output=False)
    def refuses_politely() -> str:
        """Reports failure in the response TEXT while the protocol-level
        isError flag stays false - postgres-mcp's restricted mode does
        exactly this."""
        return "Error: Error validating query: DELETE FROM articles"

    @server.tool(structured_output=False)
    def explodes() -> str:
        """Raises, which the server turns into a real isError result."""
        raise RuntimeError("tool blew up")

    @server.tool(structured_output=False)
    async def slow() -> str:
        await asyncio.sleep(5)
        return "too late"

    @server.tool(structured_output=False)
    def gibberish() -> str:
        return "this is neither JSON nor a Python literal {{{"

    return server


def connected_session():
    """Opened inside each test rather than in a fixture.

    An `async with` here spans an anyio task group, and pytest-asyncio runs
    async-generator fixture setup and teardown in *different tasks* - which
    makes the exit fail with "Attempted to exit cancel scope in a different
    task than it was entered in". That is the same constraint that forces
    the stdio supervisor in app/services/mcp/registry.py to own its session
    for the session's whole lifetime.
    """
    return create_connected_server_and_client_session(build_server()._mcp_server)


async def test_list_tool_names_returns_every_tool():
    async with connected_session() as session:
        names = await list_tool_names(session)
    assert {"echo_json", "echo_repr", "refuses_politely", "explodes", "slow"} <= set(names)


async def test_json_payload_is_parsed():
    async with connected_session() as session:
        result = await call_tool(session, "echo_json", {"value": "hello"}, timeout=5)
    assert result.is_error is False
    assert result.payload() == {"items": [{"id": 1, "title": "hello"}]}


async def test_python_repr_payload_is_parsed():
    """The JSON branch fails on single quotes and None; the literal_eval
    fallback is what makes postgres-mcp readable at all."""
    async with connected_session() as session:
        result = await call_tool(session, "echo_repr", {}, timeout=5)
    assert result.payload() == [{"id": 1, "title": "from repr", "author": None}]


async def test_unreadable_payload_returns_none_rather_than_raising():
    """A source returning a shape we cannot read must contribute nothing -
    not break the whole federated search."""
    async with connected_session() as session:
        result = await call_tool(session, "gibberish", {}, timeout=5)
    assert result.payload() is None


async def test_tool_exception_becomes_mcp_tool_error():
    async with connected_session() as session:
        with pytest.raises(McpToolError):
            await call_tool(session, "explodes", {}, timeout=5)


async def test_text_error_prefix_is_ignored_by_default():
    """Opt-in, deliberately: a code search for the string "Error:" would
    otherwise have every hit rejected."""
    async with connected_session() as session:
        result = await call_tool(session, "refuses_politely", {}, timeout=5)
    assert result.is_error is False
    assert result.text.startswith("Error:")


async def test_text_error_prefix_is_honoured_when_requested():
    async with connected_session() as session:
        with pytest.raises(McpToolError):
            await call_tool(session, "refuses_politely", {}, timeout=5, text_error_prefixes=("Error:",))


async def test_timeout_raises_mcp_error():
    async with connected_session() as session:
        with pytest.raises(McpError):
            await call_tool(session, "slow", {}, timeout=0.2)


@pytest.mark.parametrize(
    "raw,expected",
    [
        (RawToolResult(structured={"a": 1}, text="ignored"), {"a": 1}),
        (RawToolResult(text='{"a": 1}'), {"a": 1}),
        (RawToolResult(text="{'a': 1}"), {"a": 1}),
        (RawToolResult(text=""), None),
        (RawToolResult(text="not parseable ["), None),
        # A repr carrying a constructor call is NOT a literal, so it must
        # fail closed rather than being evaluated. This is why generated SQL
        # is wrapped in json_agg - see app/services/mcp/postgres.py.
        (RawToolResult(text="[{'ts': datetime.datetime(2026, 1, 1)}]"), None),
    ],
)
def test_payload_ladder(raw, expected):
    assert raw.payload() == expected


def test_structured_content_wins_over_text():
    raw = RawToolResult(structured=[{"id": 1}], text='{"id": 2}')
    assert raw.payload() == [{"id": 1}]
