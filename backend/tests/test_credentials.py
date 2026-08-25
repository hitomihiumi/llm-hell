"""Per-user credentials, and the rules that make storing them acceptable.

Until this existed the rule was absolute - secrets live in the environment,
and a row records only the name of the variable to read. Storing a user's own
Google and GitLab tokens narrows that rule, and these tests are what hold the
narrowed version in place:

  * what goes into the column is ciphertext, never the token;
  * no route can return a secret, because the response model has nowhere to
    put one;
  * a key that has been rotated costs the credential, never the search.

The last one is the difference between a feature and an outage. A deployment
that has always used deployment-wide tokens must keep working exactly as it
did, so every failure in this layer falls back rather than raising.
"""

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.crypto import CredentialCipher, CredentialEncryptionError, generate_key
from app.models.user import User
from app.models.user_credential import UserCredential
from app.services import credentials as credential_service

KEY = generate_key()
TOKEN = "glpat-not-a-real-token-000000000000"


@pytest_asyncio.fixture
async def db(test_db_engine):
    maker = async_sessionmaker(test_db_engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as session:
        yield session


@pytest_asyncio.fixture
async def user(db):
    person = User(username="tester", email="tester@example.com", password_hash="x", role="user")
    db.add(person)
    await db.commit()
    await db.refresh(person)
    return person


def settings(**overrides) -> Settings:
    return Settings(credentials_encryption_key=KEY, **overrides)


# --- the cipher ----------------------------------------------------------------


def test_a_round_trip_returns_what_went_in():
    cipher = CredentialCipher(KEY)

    assert cipher.decrypt(cipher.encrypt(TOKEN)) == TOKEN


def test_the_ciphertext_does_not_contain_the_token():
    """The whole point of the column. A dump that greps for `glpat-` finds
    nothing."""
    ciphertext = CredentialCipher(KEY).encrypt(TOKEN)

    assert TOKEN not in ciphertext
    assert "glpat" not in ciphertext


def test_encrypting_twice_gives_different_ciphertext():
    """Fernet carries a random IV, so two users with the same token do not
    have the same row - which would otherwise be a way to learn that they
    do."""
    cipher = CredentialCipher(KEY)

    assert cipher.encrypt(TOKEN) != cipher.encrypt(TOKEN)


def test_another_key_cannot_read_it():
    ciphertext = CredentialCipher(KEY).encrypt(TOKEN)

    with pytest.raises(CredentialEncryptionError):
        CredentialCipher(generate_key()).decrypt(ciphertext)


def test_a_missing_key_says_what_to_do_about_it():
    with pytest.raises(CredentialEncryptionError, match="CREDENTIALS_ENCRYPTION_KEY"):
        CredentialCipher("")


def test_a_key_that_is_not_a_key_is_refused_at_construction():
    """Rather than at the first store, which would be during a user's
    sign-in and long after the operator stopped watching."""
    with pytest.raises(CredentialEncryptionError, match="valid Fernet key"):
        CredentialCipher("hunter2")


# --- the store -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_stored_credential_comes_back(db, user):
    await credential_service.store(
        db, user=user, provider="gitlab", secret=TOKEN, settings=settings(), account="tester"
    )

    assert await credential_service.secret_for(
        db, user=user, provider="gitlab", settings=settings()
    ) == TOKEN


@pytest.mark.asyncio
async def test_what_lands_in_the_column_is_not_the_token(db, user):
    await credential_service.store(db, user=user, provider="gitlab", secret=TOKEN, settings=settings())

    row = (await db.execute(select(UserCredential))).scalar_one()
    assert row.secret != TOKEN
    assert TOKEN not in row.secret


@pytest.mark.asyncio
async def test_reconnecting_replaces_rather_than_accumulates(db, user):
    """Otherwise there is a question about which of three tokens a search
    used, and no way to answer it."""
    await credential_service.store(db, user=user, provider="gitlab", secret="first", settings=settings())
    await credential_service.store(db, user=user, provider="gitlab", secret="second", settings=settings())

    rows = (await db.execute(select(UserCredential))).scalars().all()
    assert len(rows) == 1
    assert await credential_service.secret_for(
        db, user=user, provider="gitlab", settings=settings()
    ) == "second"


@pytest.mark.asyncio
async def test_a_rotated_key_costs_the_credential_and_not_the_search(db, user):
    """The failure that matters. An operator who rotates the key must get a
    user asked to reconnect, not a stack trace in the middle of a search."""
    await credential_service.store(db, user=user, provider="gitlab", secret=TOKEN, settings=settings())

    rotated = Settings(credentials_encryption_key=generate_key())
    assert await credential_service.secret_for(db, user=user, provider="gitlab", settings=rotated) is None


@pytest.mark.asyncio
async def test_no_key_configured_means_no_per_user_credentials_at_all(db, user):
    """Not an error: it is how a single-tenant install runs, and the
    connectors fall back to the deployment's own tokens."""
    off = Settings(credentials_encryption_key="")

    assert await credential_service.secret_for(db, user=user, provider="gitlab", settings=off) is None
    with pytest.raises(CredentialEncryptionError):
        await credential_service.store(db, user=user, provider="gitlab", secret=TOKEN, settings=off)


@pytest.mark.asyncio
async def test_a_provider_nobody_connected_is_not_an_error(db, user):
    assert await credential_service.secret_for(
        db, user=user, provider="google", settings=settings()
    ) is None


@pytest.mark.asyncio
async def test_an_unknown_provider_is_refused_before_anything_is_written(db, user):
    with pytest.raises(ValueError, match="unknown credential provider"):
        await credential_service.store(db, user=user, provider="dropbox", secret="x", settings=settings())


@pytest.mark.asyncio
async def test_forgetting_removes_it(db, user):
    await credential_service.store(db, user=user, provider="gitlab", secret=TOKEN, settings=settings())

    assert await credential_service.forget(db, user=user, provider="gitlab") is True
    assert await credential_service.forget(db, user=user, provider="gitlab") is False
    assert await credential_service.secret_for(
        db, user=user, provider="gitlab", settings=settings()
    ) is None


# --- what the interface may see -------------------------------------------------


@pytest.mark.asyncio
async def test_status_lists_every_provider_including_the_unconnected(db, user):
    """"You have not connected GitLab" is the most useful thing this can say,
    and an absent row would render as an absent list item."""
    report = await credential_service.status(db, user=user, settings=settings())

    assert {entry["provider"] for entry in report} == {"google", "gitlab"}
    assert all(entry["connected"] is False for entry in report)


@pytest.mark.asyncio
async def test_status_never_carries_a_secret(db, user):
    await credential_service.store(
        db, user=user, provider="gitlab", secret=TOKEN, settings=settings(), account="tester"
    )

    report = await credential_service.status(db, user=user, settings=settings())

    assert TOKEN not in repr(report)
    assert "secret" not in repr(report)


@pytest.mark.asyncio
async def test_status_says_who_the_credential_speaks_for(db, user):
    """So a user can tell whose Drive a search is reading."""
    await credential_service.store(
        db, user=user, provider="google", secret="tester@example.com",
        settings=settings(), account="tester@example.com",
    )

    entry = next(
        item
        for item in await credential_service.status(db, user=user, settings=settings())
        if item["provider"] == "google"
    )
    assert entry["connected"] is True
    assert entry["account"] == "tester@example.com"


@pytest.mark.asyncio
async def test_status_carries_the_servers_own_gitlab_url(db, user):
    """A local dev GitLab's port drifts on every container restart. The
    extension's "authorization window" prefills from this rather than a
    remembered value, so it never points at a URL that no longer exists."""
    report = await credential_service.status(
        db, user=user, settings=settings(gitlab_web_url="http://localhost:32769")
    )

    entry = next(item for item in report if item["provider"] == "gitlab")
    assert entry["detail"]["server_web_url"] == "http://localhost:32769"


@pytest.mark.asyncio
async def test_status_says_when_storage_is_switched_off(db, user):
    """The interface needs it to explain why connecting does nothing, rather
    than showing a button that silently fails."""
    report = await credential_service.status(db, user=user, settings=Settings(credentials_encryption_key=""))

    assert all(entry["storage_available"] is False for entry in report)


@pytest.mark.asyncio
async def test_one_users_credential_is_not_anothers(db, user):
    other = User(username="someone-else", email="other@example.com", password_hash="x", role="user")
    db.add(other)
    await db.commit()
    await db.refresh(other)

    await credential_service.store(db, user=user, provider="gitlab", secret=TOKEN, settings=settings())

    assert await credential_service.secret_for(
        db, user=other, provider="gitlab", settings=settings()
    ) is None
