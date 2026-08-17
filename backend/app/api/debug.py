"""Raw MCP tool calls, for checking a server's response shape after a
deployment or an upgrade.

Off unless `ENABLE_MCP_DEBUG=true`, and admin-only when on: it returns
unfiltered payloads straight from a source, which is exactly the data the
rest of the application is careful about.

`tools/mcp_probe.py` does the same thing from a shell and needs neither the
app nor the database; this exists for checking a deployed environment where
running the probe is awkward.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config import get_settings
from app.core.sessions import AdminUser, require_csrf
from app.schemas.debug import McpCallIn, McpCallOut
from app.services.mcp.gitlab import GitLabConnector
from app.services.mcp.postgres import PostgresKbConnector
from app.services.mcp.registry import McpRegistry, get_mcp_registry
from app.services.mcp.transport import call_tool, http_session, sse_session, summarise_exception

logger = logging.getLogger("llmhell.api.debug")

router = APIRouter(prefix="/api/debug", tags=["debug"], dependencies=[Depends(require_csrf)])


@router.post("/mcp/call", response_model=McpCallOut)
async def mcp_call(
    payload: McpCallIn,
    _: AdminUser,
    registry: McpRegistry = Depends(get_mcp_registry),
) -> McpCallOut:
    settings = get_settings()
    if not settings.enable_mcp_debug:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")

    connector = registry.get(payload.source)
    if connector is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown source: {payload.source!r}")

    # Each server speaks a different transport, and the connector is the only
    # thing that knows which.
    if isinstance(connector, GitLabConnector):
        session_factory = lambda: http_session(settings.gitlab_mcp_url, connector._headers)  # noqa: E731,SLF001
    elif isinstance(connector, PostgresKbConnector):
        session_factory = lambda: sse_session(settings.postgres_mcp_url)  # noqa: E731
    else:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "no debug transport for this source")

    try:
        async with session_factory() as session:
            raw = await call_tool(
                session, payload.tool, payload.arguments, timeout=settings.mcp_call_timeout_seconds
            )
    except Exception as exc:  # noqa: BLE001 - a debug endpoint reports failures as data
        return McpCallOut(is_error=True, error=summarise_exception(exc))

    return McpCallOut(
        is_error=raw.is_error,
        structured=raw.structured,
        text=raw.text,
        blocks=raw.blocks,
        parsed=raw.payload(source=payload.source, tool=payload.tool),
    )
