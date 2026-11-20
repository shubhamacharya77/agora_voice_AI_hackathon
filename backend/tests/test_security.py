"""Unit tests for passwords, tokens and the permission table (no database needed)."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from pydantic import ValidationError

from app.auth.passwords import hash_password, verify_password
from app.auth.permissions import Permission, has_permission, permissions_for
from app.auth.tokens import InvalidTokenError, create_access_token, decode_access_token
from app.config import Settings
from app.models import Role

SECRET = "unit-test-secret-that-is-long-enough-0123456789"


def make_settings(**overrides: object) -> Settings:
    values = {"database_url": "postgresql+psycopg://u:p@localhost:1/x", "jwt_secret": SECRET}
    return Settings(_env_file=None, **(values | overrides))


# --- passwords ---

def test_password_is_stored_as_argon2_hash_and_verifies() -> None:
    hashed = hash_password("s3cret pass")
    assert hashed.startswith("$argon2")
    assert "s3cret pass" not in hashed
    assert verify_password("s3cret pass", hashed)


def test_wrong_password_or_garbage_hash_fails_without_raising() -> None:
    hashed = hash_password("s3cret pass")
    assert not verify_password("wrong", hashed)
    assert not verify_password("s3cret pass", "not-a-hash")


# --- tokens ---

def test_token_round_trip_returns_user_id() -> None:
    settings = make_settings()
    user_id = uuid.uuid4()
    token, lifetime = create_access_token(user_id, settings)
    assert lifetime == settings.access_token_ttl_minutes * 60
    assert decode_access_token(token, settings) == user_id


def test_expired_token_is_rejected() -> None:
    settings = make_settings()
    long_ago = datetime.now(UTC) - timedelta(days=2)
    token, _ = create_access_token(uuid.uuid4(), settings, now=long_ago)
    with pytest.raises(InvalidTokenError):
        decode_access_token(token, settings)


def test_token_signed_with_another_secret_is_rejected() -> None:
    forged, _ = create_access_token(uuid.uuid4(), make_settings(jwt_secret="x" * 40))
    with pytest.raises(InvalidTokenError):
        decode_access_token(forged, make_settings())


def test_token_without_subject_or_with_non_uuid_subject_is_rejected() -> None:
    settings = make_settings()
    now = datetime.now(UTC)
    no_sub = jwt.encode({"iat": now, "exp": now + timedelta(hours=1)}, SECRET, algorithm="HS256")
    bad_sub = jwt.encode(
        {"sub": "admin", "iat": now, "exp": now + timedelta(hours=1)}, SECRET, algorithm="HS256"
    )
    for token in (no_sub, bad_sub):
        with pytest.raises(InvalidTokenError):
            decode_access_token(token, settings)


def test_short_jwt_secret_is_refused_at_startup() -> None:
    with pytest.raises(ValidationError):
        make_settings(jwt_secret="too-short")


# --- permissions ---

ADMIN_ONLY = [
    Permission.KNOWLEDGE_READ_RESTRICTED,
    Permission.EMAIL_READ,
    Permission.EMAIL_SEND,
    Permission.HR_READ_ALL,
    Permission.MEETINGS_MANAGE,
    Permission.INVENTORY_MANAGE,
    Permission.TICKETS_MANAGE,
]


@pytest.mark.parametrize("permission", ADMIN_ONLY)
def test_employee_does_not_get_admin_permissions(permission: Permission) -> None:
    assert not has_permission(Role.EMPLOYEE, permission)
    assert has_permission(Role.ADMIN, permission)


def test_admin_can_do_everything_an_employee_can() -> None:
    assert permissions_for(Role.EMPLOYEE) <= permissions_for(Role.ADMIN)


def test_every_role_has_a_permission_set() -> None:
    for role in Role:
        assert permissions_for(role), f"{role} has no permissions defined"
