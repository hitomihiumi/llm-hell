"""Listing, tuning and checking the search sources."""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.sessions import AdminUser, CurrentUser, require_csrf
from app.models.source import Source
from app.schemas.sources import SourceHealthOut, SourceOut, SourceUpdateIn
from app.services.mcp.registry import McpRegistry, get_mcp_registry

logger = logging.getLogger("llmhell.api.sources")

router = APIRouter(prefix="/api/sources", tags=["sources"], dependencies=[Depends(require_csrf)])


async def _get(db: AsyncSession, key: str) -> Source:
    source = (await db.execute(select(Source).where(Source.key == key))).scalar_one_or_none()
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"unknown source: {key!r}")
    return source


@router.get("", response_model=list[SourceOut])
async def list_sources(_: CurrentUser, db: AsyncSession = Depends(get_db)) -> list[Source]:
    """Every source, including disabled ones - the UI renders those as
    switched-off filter chips rather than hiding them, so it is visible that
    a source exists but is not being searched."""
    return list((await db.execute(select(Source).order_by(Source.key))).scalars().all())


@router.patch("/{key}", response_model=SourceOut)
async def update_source(
    key: str,
    payload: SourceUpdateIn,
    _: AdminUser,
    db: AsyncSession = Depends(get_db),
) -> Source:
    source = await _get(db, key)
    # exclude_unset so omitting a field leaves it alone, rather than
    # resetting it to the schema default.
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(source, field, value)
    await db.commit()
    await db.refresh(source)
    return source


@router.post("/{key}/check", response_model=SourceHealthOut)
async def check_source(
    key: str,
    _: AdminUser,
    db: AsyncSession = Depends(get_db),
    registry: McpRegistry = Depends(get_mcp_registry),
) -> SourceHealthOut:
    """Ask the source's server what it can actually do, and store the answer.

    This is the only way to learn several things configuration cannot tell
    us - whether a GitLab instance supports code search, whether the search
    toolset is even enabled - so the result is persisted on the row for the
    admin page to display.
    """
    source = await _get(db, key)
    connector = registry.get(key)
    if connector is None:
        result = {"ok": False, "error": "no connector is registered for this source"}
    else:
        result = await connector.health()

    checked_at = datetime.now(UTC)
    source.last_checked_at = checked_at
    source.last_check_result = result
    await db.commit()

    return SourceHealthOut(key=key, ok=bool(result.get("ok")), checked_at=checked_at, result=result)
