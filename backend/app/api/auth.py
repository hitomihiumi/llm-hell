"""Login / logout / whoami for the web app.

Three routes, all cookie-based. The API-key routes under `/v1/*` are
unaffected and share nothing with these beyond the `User` table.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import hash_password, verify_password
from app.core.sessions import (
    CurrentUser,
    create_session,
    generate_csrf_token,
    require_csrf,
    revoke_session,
)
from app.models.user import User
from app.schemas.auth import LoginIn, UserOut

router = APIRouter(prefix="/api/auth", tags=["auth"])

# A real argon2 hash of a value nobody has, verified against when the
# username does not exist or carries no password. Without it, a missing user
# returns in microseconds while a real one takes ~50-100 ms, and that gap is
# a usable account-enumeration oracle.
_DUMMY_HASH = hash_password("dummy-password-for-constant-time-login")


def _set_session_cookies(response: Response, raw_token: str, csrf_token: str) -> None:
    settings = get_settings()
    # No `domain=`: a host-only cookie is not sent to subdomains, which is
    # what we want. `samesite="lax"` is enough for the frontend even on a
    # different port, because a port is not part of a "site" - :3001 and
    # :8000 on localhost are same-site, only cross-origin.
    response.set_cookie(
        key=settings.session_cookie_name,
        value=raw_token,
        httponly=True,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
        max_age=settings.session_ttl_seconds,
    )
    # Deliberately NOT httponly - the frontend has to read this one to echo
    # it back in the X-CSRF-Token header. It is not a credential on its own:
    # it authenticates nothing, it only proves the caller can read cookies
    # for this origin.
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=csrf_token,
        httponly=False,
        samesite="lax",
        secure=settings.session_cookie_secure,
        path="/",
        max_age=settings.session_ttl_seconds,
    )


@router.post("/login", response_model=UserOut)
async def login(
    payload: LoginIn,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> User:
    user = (
        await db.execute(select(User).where(User.username == payload.username))
    ).scalar_one_or_none()

    # Run the verification either way, then decide - see _DUMMY_HASH.
    stored_hash = user.password_hash if user is not None and user.password_hash else _DUMMY_HASH
    password_ok = verify_password(payload.password, stored_hash)

    if user is None or not user.password_hash or not password_ok or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")

    _, raw_token = await create_session(
        db,
        user,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )
    _set_session_cookies(response, raw_token, generate_csrf_token())
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_csrf)])
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)) -> Response:
    settings = get_settings()
    await revoke_session(db, request.cookies.get(settings.session_cookie_name, ""))
    # delete_cookie must repeat path/samesite/secure or the browser treats it
    # as a different cookie and leaves the original in place.
    for name in (settings.session_cookie_name, settings.csrf_cookie_name):
        response.delete_cookie(
            key=name, path="/", samesite="lax", secure=settings.session_cookie_secure
        )
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=UserOut)
async def me(current: CurrentUser) -> User:
    return current
