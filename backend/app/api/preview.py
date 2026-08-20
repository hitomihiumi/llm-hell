"""Pictures of the pages a result was actually read from.

The point is verifiability, not decoration. An answer can say the USB port sits
to the left of the MCU, and the reader has no way to check that from a text
snippet — the claim came from a picture, so the picture is what has to be
shown. These are the same rendered pages that went into the answer prompt,
produced by the same code at the same scale, so what the reader sees is what
the model saw rather than a separate rendering that might differ.

Deliberately one image per request rather than a bundle of data URIs in the
JSON: a browser caches them, requests them lazily as they scroll into view,
and never holds four base64 blobs in a search response it mostly will not
look at.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.core.sessions import CurrentUser
from app.models.source import SOURCE_GOOGLE_DRIVE
from app.services.mcp.google import GoogleWorkspaceConnector
from app.services.mcp.registry import McpRegistry, get_mcp_registry
from app.services.mcp.transport import summarise_exception

logger = logging.getLogger("llmhell.api.preview")

router = APIRouter(prefix="/api/preview", tags=["preview"])

# A page is immutable for a given document version, and the fingerprint is
# part of what produced it, so this can be cached hard. An edited document
# gets different pages under the same URL, which is why this is private and
# not public: a shared cache must not serve one reader's document to another.
CACHE_CONTROL = "private, max-age=3600"


@router.get("/{hit_id:path}/{page}", response_class=Response)
async def get_page(
    hit_id: str,
    page: int,
    _: CurrentUser,
    registry: McpRegistry = Depends(get_mcp_registry),
) -> Response:
    if hit_id.split(":", 1)[0] != SOURCE_GOOGLE_DRIVE:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "nothing to preview for this result")

    connector = registry.get(SOURCE_GOOGLE_DRIVE)
    if not isinstance(connector, GoogleWorkspaceConnector):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "that source is not configured")

    try:
        pages = await connector.page_images(hit_id)
    except Exception as exc:  # noqa: BLE001 - a source failing is not a server error
        logger.warning("preview failed for %s: %s", hit_id, summarise_exception(exc))
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "the source could not be reached") from exc

    if page < 0 or page >= len(pages):
        # Same answer for "no such page" and "not a document with pages", so
        # this cannot be used to probe what exists.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "nothing to preview for this result")

    return Response(
        content=pages[page],
        media_type="image/jpeg",
        headers={"Cache-Control": CACHE_CONTROL},
    )
