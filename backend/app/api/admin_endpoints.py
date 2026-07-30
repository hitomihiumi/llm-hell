from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import AdminUser
from app.models.endpoint import DEFAULT_REASONING_PROFILE, ModelEndpoint
from app.schemas.endpoint import (
    EndpointCheckOut,
    EndpointCreateRequest,
    EndpointOut,
    EndpointUpdateRequest,
)
from app.services.llm.probe import check_endpoint

router = APIRouter(prefix="/api/admin/endpoints", tags=["admin"])


def _to_out(endpoint: ModelEndpoint) -> EndpointOut:
    return EndpointOut(
        id=endpoint.id,
        name=endpoint.name,
        base_url=endpoint.base_url,
        has_api_key=bool(endpoint.api_key),
        model_id=endpoint.model_id,
        role=endpoint.role,
        ctx_window=endpoint.ctx_window,
        price_per_mtok_in=endpoint.price_per_mtok_in,
        price_per_mtok_out=endpoint.price_per_mtok_out,
        enabled=endpoint.enabled,
        tools_mode=endpoint.tools_mode,
        reasoning_profile=endpoint.reasoning_profile,
        last_checked_at=endpoint.last_checked_at.isoformat() if endpoint.last_checked_at else None,
        last_check_result=endpoint.last_check_result,
    )


@router.get("", response_model=list[EndpointOut])
async def list_endpoints(_: AdminUser, db: AsyncSession = Depends(get_db)) -> list[EndpointOut]:
    result = await db.execute(select(ModelEndpoint).order_by(ModelEndpoint.role, ModelEndpoint.name))
    return [_to_out(e) for e in result.scalars().all()]


@router.post("", response_model=EndpointOut)
async def create_endpoint(
    body: EndpointCreateRequest, _: AdminUser, db: AsyncSession = Depends(get_db)
) -> EndpointOut:
    endpoint = ModelEndpoint(
        name=body.name,
        base_url=body.base_url,
        api_key=body.api_key,
        model_id=body.model_id,
        role=body.role,
        ctx_window=body.ctx_window,
        price_per_mtok_in=body.price_per_mtok_in,
        price_per_mtok_out=body.price_per_mtok_out,
        tools_mode=body.tools_mode,
        reasoning_profile=body.reasoning_profile or DEFAULT_REASONING_PROFILE,
    )
    db.add(endpoint)
    await db.commit()
    await db.refresh(endpoint)
    return _to_out(endpoint)


@router.patch("/{endpoint_id}", response_model=EndpointOut)
async def update_endpoint(
    endpoint_id: str, body: EndpointUpdateRequest, _: AdminUser, db: AsyncSession = Depends(get_db)
) -> EndpointOut:
    endpoint = await db.get(ModelEndpoint, endpoint_id)
    if endpoint is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ендпоінт не знайдено")

    for field_name, value in body.model_dump(exclude_unset=True).items():
        setattr(endpoint, field_name, value)

    await db.commit()
    await db.refresh(endpoint)
    return _to_out(endpoint)


@router.delete("/{endpoint_id}")
async def delete_endpoint(endpoint_id: str, _: AdminUser, db: AsyncSession = Depends(get_db)) -> dict[str, bool]:
    endpoint = await db.get(ModelEndpoint, endpoint_id)
    if endpoint is not None:
        await db.delete(endpoint)
        await db.commit()
    return {"ok": True}


@router.post("/{endpoint_id}/check", response_model=EndpointCheckOut)
async def check_endpoint_route(
    endpoint_id: str, _: AdminUser, db: AsyncSession = Depends(get_db)
) -> EndpointCheckOut:
    endpoint = await db.get(ModelEndpoint, endpoint_id)
    if endpoint is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ендпоінт не знайдено")

    report = await check_endpoint(endpoint)
    report_dict = report.to_dict()

    endpoint.last_checked_at = datetime.now(timezone.utc)
    endpoint.last_check_result = report_dict
    await db.commit()

    return EndpointCheckOut(**report_dict)
