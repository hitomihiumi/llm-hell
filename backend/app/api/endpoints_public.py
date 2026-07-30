"""What a regular tester needs to know about model endpoints to start a
run: just the enabled ones, by role, with nothing sensitive (api_key,
base_url, reasoning_profile internals) - that's admin-only territory
(`app.api.admin_endpoints`).
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import CurrentUser
from app.models.endpoint import ModelEndpoint
from app.schemas.endpoint_public import ActiveEndpointOut

router = APIRouter(prefix="/api/endpoints", tags=["endpoints"])


@router.get("", response_model=list[ActiveEndpointOut])
async def list_active_endpoints(_: CurrentUser, db: AsyncSession = Depends(get_db)) -> list[ModelEndpoint]:
    result = await db.execute(
        select(ModelEndpoint).where(ModelEndpoint.enabled.is_(True)).order_by(ModelEndpoint.role, ModelEndpoint.name)
    )
    return list(result.scalars().all())
