import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import AdminUser
from app.models.user import Invite
from app.schemas.auth import InviteCreateRequest, InviteOut

router = APIRouter(prefix="/api/admin/invites", tags=["admin"])


def _generate_code() -> str:
    return secrets.token_urlsafe(9).upper().replace("_", "").replace("-", "")


@router.get("", response_model=list[InviteOut])
async def list_invites(_: AdminUser, db: AsyncSession = Depends(get_db)) -> list[Invite]:
    result = await db.execute(select(Invite).order_by(Invite.created_at.desc()))
    return list(result.scalars().all())


@router.post("", response_model=InviteOut)
async def create_invite(body: InviteCreateRequest, _: AdminUser, db: AsyncSession = Depends(get_db)) -> Invite:
    expires_at = None
    if body.expires_in_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)

    invite = Invite(code=_generate_code(), role=body.role, expires_at=expires_at)
    db.add(invite)
    await db.commit()
    await db.refresh(invite)
    return invite


@router.delete("/{invite_id}")
async def delete_invite(invite_id: str, _: AdminUser, db: AsyncSession = Depends(get_db)) -> dict[str, bool]:
    invite = await db.get(Invite, invite_id)
    if invite is not None:
        await db.delete(invite)
        await db.commit()
    return {"ok": True}
