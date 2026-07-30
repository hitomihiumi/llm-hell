from datetime import datetime, timezone

import jwt
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.deps import ACCESS_COOKIE_NAME, REFRESH_COOKIE_NAME, CurrentUser
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.user import Invite, User
from app.schemas.auth import LoginRequest, RegisterRequest, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()


def _set_auth_cookies(response: Response, user: User) -> None:
    access = create_access_token(user.id, user.role)
    refresh = create_refresh_token(user.id, user.role)
    common = dict(httponly=True, secure=settings.cookie_secure, samesite="lax", path="/")
    response.set_cookie(ACCESS_COOKIE_NAME, access, max_age=settings.access_token_ttl_minutes * 60, **common)
    response.set_cookie(REFRESH_COOKIE_NAME, refresh, max_age=settings.refresh_token_ttl_days * 86400, **common)


@router.post("/register", response_model=UserOut)
async def register(body: RegisterRequest, response: Response, db: AsyncSession = Depends(get_db)) -> User:
    invite = (await db.execute(select(Invite).where(Invite.code == body.invite_code))).scalar_one_or_none()
    if invite is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Інвайт-код не знайдено")
    if invite.used_by is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Інвайт-код вже використано")
    if invite.expires_at is not None and invite.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Термін дії інвайт-коду закінчився")

    existing = (await db.execute(select(User).where(User.username == body.username))).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ім'я користувача вже зайняте")

    user = User(username=body.username, password_hash=hash_password(body.password), role=invite.role)
    db.add(user)
    await db.flush()

    invite.used_by = user.id
    invite.used_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(user)

    _set_auth_cookies(response, user)
    return user


@router.post("/login", response_model=UserOut)
async def login(body: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)) -> User:
    user = (await db.execute(select(User).where(User.username == body.username))).scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Невірне ім'я користувача або пароль")

    _set_auth_cookies(response, user)
    return user


@router.post("/refresh", response_model=UserOut)
async def refresh(
    response: Response,
    db: AsyncSession = Depends(get_db),
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE_NAME),
) -> User:
    if not refresh_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Немає refresh-токена")
    try:
        payload = decode_token(refresh_token, "refresh")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Недійсний refresh-токен")

    user = (await db.execute(select(User).where(User.id == payload["sub"]))).scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Користувача не знайдено")

    _set_auth_cookies(response, user)
    return user


@router.post("/logout")
async def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(ACCESS_COOKIE_NAME, path="/")
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> User:
    return user
