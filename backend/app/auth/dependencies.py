"""FastAPI dependencies that identify the caller and enforce permissions.

Identity always comes from the bearer token, verified on the server. Nothing the user
says (or the AI generates) can change who the caller is or what they may do.
"""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.permissions import Permission, has_permission
from app.auth.tokens import InvalidTokenError, decode_access_token
from app.deps import SessionDep, SettingsDep
from app.models import User

bearer_scheme = HTTPBearer(
    auto_error=False,
    description="Access token from POST /auth/login or POST /auth/demo-login",
)


def unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    session: SessionDep,
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    if credentials is None:
        raise unauthorized("Not authenticated")
    try:
        user_id = decode_access_token(credentials.credentials, settings)
    except InvalidTokenError:
        raise unauthorized("Invalid or expired token") from None
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise unauthorized("Invalid or expired token")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_permission(permission: Permission) -> Callable[..., Awaitable[User]]:
    """Dependency factory: `Depends(require_permission(Permission.EMAIL_SEND))`."""

    async def _require(user: CurrentUser) -> User:
        if not has_permission(user.role, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to do that",
            )
        return user

    return _require
