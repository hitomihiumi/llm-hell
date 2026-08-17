"""Resolves the synthesised `/records/{table}/{pk}` links a Postgres hit
carries, since a database row has no natural URL.

The table name is matched against the configured whitelist inside the
connector and never interpolated from the request, and the primary key is
escaped and length-capped. Without both of those this route would be an
arbitrary-read primitive over everything the KB role can see.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.sessions import CurrentUser
from app.models.source import SOURCE_POSTGRES_KB
from app.schemas.records import RecordOut
from app.services.mcp.postgres import PostgresKbConnector
from app.services.mcp.registry import McpRegistry, get_mcp_registry
from app.services.mcp.transport import summarise_exception

logger = logging.getLogger("llmhell.api.records")

router = APIRouter(prefix="/api/records", tags=["records"])


@router.get("/{table}/{pk}", response_model=RecordOut)
async def get_record(
    table: str,
    pk: str,
    _: CurrentUser,
    registry: McpRegistry = Depends(get_mcp_registry),
) -> RecordOut:
    connector = registry.get(SOURCE_POSTGRES_KB)
    if not isinstance(connector, PostgresKbConnector):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "the knowledge-base source is not configured")

    try:
        row = await connector.fetch_record(table, pk)
    except Exception as exc:  # noqa: BLE001
        logger.warning("record lookup failed for %s/%s: %s", table, pk, summarise_exception(exc))
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "the knowledge base could not be reached") from exc

    if row is None:
        # Same response for "not a whitelisted table" and "no such row":
        # distinguishing them would confirm which tables exist.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "record not found")

    return RecordOut(table=table, pk=pk, fields=row)
