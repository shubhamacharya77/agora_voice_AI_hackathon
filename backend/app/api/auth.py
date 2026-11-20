import logging
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.demo import DEMO_USERS
from app.auth.dependencies import CurrentUser, unauthorized
from app.auth.passwords import burn_verification_time, verify_password
from app.auth.permissions import Permission, permissions_for
from app.auth.tokens import create_access_token
from app.config import Settings
from app.deps import SessionDep, SettingsDep
from app.models import Role, User, UserBase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class DemoLoginRequest(BaseModel):
    role: Role


class UserPublic(UserBase):
    """A user as the API shows it: never the password hash."""

    id: uuid.UUID
    permissions: list[Permission]


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int  # seconds
    user: UserPublic


def to_public(user: User) -> UserPublic:
    return UserPublic(
        id=user.id,
        email=user.email,
        name=user.name,
        role=user.role,
        permissions=sorted(permissions_for(user.role)),
    )


def _token_response(user: User, settings: Settings) -> TokenResponse:
    token, expires_in = create_access_token(user.id, settings)
    return TokenResponse(access_token=token, expires_in=expires_in, user=to_public(user))


async def _find_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.exec(select(User).where(User.email == email))
    return result.first()


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, session: SessionDep, settings: SettingsDep) -> TokenResponse:
    user = await _find_by_email(session, body.email.strip().lower())
    # argon2 is deliberately slow, so verification runs off the event loop.
    if user is None:
        await run_in_threadpool(burn_verification_time, body.password)
        password_ok = False
    else:
        password_ok = await run_in_threadpool(verify_password, body.password, user.password_hash)
    if user is None or not password_ok or not user.is_active:
        logger.warning("password login rejected")
        raise unauthorized("Incorrect email or password")
    logger.info("password login user_id=%s role=%s", user.id, user.role)
    return _token_response(user, settings)


@router.post("/demo-login", response_model=TokenResponse)
async def demo_login(
    body: DemoLoginRequest, session: SessionDep, settings: SettingsDep
) -> TokenResponse:
    """One-click login as the demo admin or employee. Only works when DEMO_LOGIN_ENABLED=true."""
    if not settings.demo_login_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    user = await _find_by_email(session, DEMO_USERS[body.role].email)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Demo users aren't set up yet. Run: uv run python -m app.seed",
        )
    logger.info("demo login user_id=%s role=%s", user.id, user.role)
    return _token_response(user, settings)


@router.get("/me", response_model=UserPublic)
async def me(user: CurrentUser) -> UserPublic:
    return to_public(user)
