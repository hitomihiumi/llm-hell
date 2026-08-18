"""Full text behind a search hit, for reading rather than skimming.

A card shows a 400-character excerpt centred on the match, which is the right
size for deciding whether a result is relevant and the wrong size for actually
using it. "Summarise the auth-service README" cannot be answered from an
excerpt, and following the external link means leaving the app and having
credentials for whatever is on the other end.

The hit id decides what gets read, so it is validated by the connector that
minted it rather than interpreted here: this route knows only which source a
prefix belongs to. A source with nothing extra to show - Gmail, whose search
already returns the message body - simply has no handler and answers 404.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.sessions import CurrentUser
from app.models.source import SOURCE_GITLAB, SOURCE_GOOGLE_DRIVE, SOURCE_POSTGRES_KB
from app.schemas.content import ContentOut
from app.services.mcp.gitlab import GitLabConnector
from app.services.mcp.google import GoogleWorkspaceConnector
from app.services.mcp.postgres import PostgresKbConnector
from app.services.mcp.registry import McpRegistry, get_mcp_registry
from app.services.mcp.transport import summarise_exception

logger = logging.getLogger("llmhell.api.content")

router = APIRouter(prefix="/api/content", tags=["content"])

# Which connector owns which id prefix. The prefix is the source key the
# connector stamped into the id, so this cannot drift from what search built.
_READABLE = (SOURCE_GITLAB, SOURCE_GOOGLE_DRIVE, SOURCE_POSTGRES_KB)


@router.get("/{hit_id:path}", response_model=ContentOut)
async def get_content(
    hit_id: str,
    _: CurrentUser,
    registry: McpRegistry = Depends(get_mcp_registry),
) -> ContentOut:
    source_key = hit_id.split(":", 1)[0]
    if source_key not in _READABLE:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "nothing to show for this result")

    connector = registry.get(source_key)
    if not isinstance(connector, GitLabConnector | GoogleWorkspaceConnector | PostgresKbConnector):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "that source is not configured")

    try:
        found = await connector.fetch_content(hit_id)
    except Exception as exc:  # noqa: BLE001 - the source failing is not a server error
        logger.warning("content lookup failed for %s: %s", hit_id, summarise_exception(exc))
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "the source could not be reached") from exc

    if found is None:
        # Same answer for "no such file" and "that id is not one of mine",
        # so this cannot be used to probe what exists.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "nothing to show for this result")

    return ContentOut(hit_id=hit_id, **found)
