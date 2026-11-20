"""JWT access tokens.

A token only says *who* the caller is (`sub` = user id). Role and permissions are always
read from the database on each request, so a role change or deactivation takes effect
immediately rather than when the token expires.
"""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from jwt import InvalidTokenError

from app.config import Settings

__all__ = ["InvalidTokenError", "create_access_token", "decode_access_token"]


def create_access_token(
    user_id: uuid.UUID, settings: Settings, *, now: datetime | None = None
) -> tuple[str, int]:
    """Return (token, lifetime in seconds). `now` exists so tests can mint expired tokens."""
    issued_at = now or datetime.now(UTC)
    lifetime = timedelta(minutes=settings.access_token_ttl_minutes)
    payload = {"sub": str(user_id), "iat": issued_at, "exp": issued_at + lifetime}
    token = jwt.encode(
        payload, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm
    )
    return token, int(lifetime.total_seconds())


def decode_access_token(token: str, settings: Settings) -> uuid.UUID:
    """Return the user id in a valid token; raise InvalidTokenError otherwise."""
    payload = jwt.decode(
        token,
        settings.jwt_secret.get_secret_value(),
        algorithms=[settings.jwt_algorithm],
        options={"require": ["exp", "iat", "sub"]},
    )
    try:
        return uuid.UUID(payload["sub"])
    except (TypeError, ValueError) as exc:
        raise InvalidTokenError("token subject is not a user id") from exc
