import jwt
import pytest

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


def test_password_hash_roundtrip() -> None:
    hashed = hash_password("correct-horse-battery-staple")
    assert verify_password("correct-horse-battery-staple", hashed)
    assert not verify_password("wrong-password", hashed)


def test_access_token_roundtrip() -> None:
    token = create_access_token("user-1", "admin")
    payload = decode_token(token, "access")
    assert payload["sub"] == "user-1"
    assert payload["role"] == "admin"


def test_access_token_rejected_as_refresh() -> None:
    token = create_access_token("user-1", "user")
    with pytest.raises(jwt.PyJWTError):
        decode_token(token, "refresh")


def test_refresh_token_roundtrip() -> None:
    token = create_refresh_token("user-2", "user")
    payload = decode_token(token, "refresh")
    assert payload["sub"] == "user-2"
