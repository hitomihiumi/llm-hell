import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.core.security import hash_password
from app.models.user import User


@pytest.fixture
def session_maker(test_db_engine):
    return async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)


async def _make_user(session_maker, *, username="alice", password="hunter2", **kwargs) -> User:
    async with session_maker() as db:
        user = User(
            username=username,
            password_hash=hash_password(password) if password is not None else None,
            **kwargs,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user


async def test_login_sets_both_cookies(api_client, session_maker):
    await _make_user(session_maker)
    settings = get_settings()

    response = await api_client.post(
        "/api/auth/login", json={"username": "alice", "password": "hunter2"}
    )

    assert response.status_code == 200
    assert response.json()["username"] == "alice"

    set_cookie = response.headers.get_list("set-cookie")
    session_cookie = next(c for c in set_cookie if c.startswith(f"{settings.session_cookie_name}="))
    csrf_cookie = next(c for c in set_cookie if c.startswith(f"{settings.csrf_cookie_name}="))

    # The session cookie must be unreadable from JavaScript; the CSRF one
    # must be readable, because the frontend has to echo it back.
    assert "httponly" in session_cookie.lower()
    assert "httponly" not in csrf_cookie.lower()
    assert "samesite=lax" in session_cookie.lower()


async def test_login_response_never_contains_the_password_hash(api_client, session_maker):
    await _make_user(session_maker)
    response = await api_client.post(
        "/api/auth/login", json={"username": "alice", "password": "hunter2"}
    )
    assert "password_hash" not in response.json()
    assert "password" not in response.json()


@pytest.mark.parametrize(
    "username,password",
    [
        ("alice", "wrong-password"),
        ("nobody", "hunter2"),
    ],
)
async def test_login_rejects_bad_credentials(api_client, session_maker, username, password):
    await _make_user(session_maker)
    response = await api_client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 401


async def test_login_failures_are_indistinguishable(api_client, session_maker):
    """A wrong password and an unknown user must produce the same response,
    or the endpoint is an account-enumeration oracle."""
    await _make_user(session_maker)
    wrong_password = await api_client.post(
        "/api/auth/login", json={"username": "alice", "password": "nope"}
    )
    unknown_user = await api_client.post(
        "/api/auth/login", json={"username": "ghost", "password": "nope"}
    )
    assert wrong_password.status_code == unknown_user.status_code == 401
    assert wrong_password.json() == unknown_user.json()


async def test_login_rejects_user_without_password(api_client, session_maker):
    """API-key-only accounts predate the web app and must not be loggable
    into with an empty or guessed password."""
    await _make_user(session_maker, username="apionly", password=None)
    response = await api_client.post(
        "/api/auth/login", json={"username": "apionly", "password": ""}
    )
    # Empty password fails schema validation before it reaches the handler.
    assert response.status_code in (401, 422)

    response = await api_client.post(
        "/api/auth/login", json={"username": "apionly", "password": "anything"}
    )
    assert response.status_code == 401


async def test_login_rejects_deactivated_user(api_client, session_maker):
    await _make_user(session_maker, username="gone", is_active=False)
    response = await api_client.post(
        "/api/auth/login", json={"username": "gone", "password": "hunter2"}
    )
    assert response.status_code == 401


async def test_me_requires_authentication(api_client):
    assert (await api_client.get("/api/auth/me")).status_code == 401


async def test_me_returns_the_logged_in_user(session_client):
    response = await session_client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["username"] == "web-user"
    assert response.json()["role"] == "user"


async def test_login_then_me_round_trip(api_client, session_maker):
    """The cookie the login route sets must be the one /me accepts - httpx
    stores it on the client, exactly as a browser would."""
    await _make_user(session_maker)
    await api_client.post("/api/auth/login", json={"username": "alice", "password": "hunter2"})

    response = await api_client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["username"] == "alice"


async def test_logout_revokes_the_session(api_client, session_maker):
    settings = get_settings()
    await _make_user(session_maker)
    login = await api_client.post("/api/auth/login", json={"username": "alice", "password": "hunter2"})
    assert login.status_code == 200

    # A browser reads the CSRF cookie and echoes it; httpx stored it for us.
    csrf = api_client.cookies.get(settings.csrf_cookie_name)
    logout = await api_client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf})
    assert logout.status_code == 204

    # Even replaying the original cookie must now fail - revocation is
    # server-side, which is the reason sessions are a table.
    assert (await api_client.get("/api/auth/me")).status_code == 401


async def test_logout_without_csrf_header_is_rejected(session_client):
    del session_client.headers["X-CSRF-Token"]
    assert (await session_client.post("/api/auth/logout")).status_code == 403


async def test_logout_with_mismatched_csrf_is_rejected(session_client):
    session_client.headers["X-CSRF-Token"] = "not-the-cookie-value"
    assert (await session_client.post("/api/auth/logout")).status_code == 403


async def test_api_key_routes_are_unaffected_by_csrf(authed_client):
    """/v1/* authenticates by bearer token and carries no cookies, so the
    CSRF dependency must not apply to it."""
    assert (await authed_client.get("/v1/models")).status_code == 200


async def test_session_cookie_does_not_authenticate_the_proxy(session_client):
    """The two auth mechanisms are deliberately separate: a web session must
    not grant access to the OpenAI-compatible proxy."""
    assert (await session_client.get("/v1/models")).status_code == 401
