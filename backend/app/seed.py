"""Create the demo admin and employee accounts. Safe to run repeatedly.

Usage (from backend/):  uv run python -m app.seed

Existing users are left exactly as they are, so changing a DEMO_*_PASSWORD later does
not update an account that already exists.
"""

import asyncio
import secrets
import sys

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.demo import DEMO_USERS, demo_password
from app.auth.passwords import hash_password
from app.config import Settings, get_settings
from app.db import Database
from app.models import User


async def seed_demo_users(session: AsyncSession, settings: Settings) -> list[str]:
    """Create any missing demo users and return the emails that were created."""
    created: list[str] = []
    for demo in DEMO_USERS.values():
        existing = (await session.exec(select(User).where(User.email == demo.email))).first()
        if existing is not None:
            continue
        # No configured password: use an unguessable one, so only demo login works.
        password = demo_password(demo.role, settings) or secrets.token_urlsafe(24)
        session.add(
            User(
                email=demo.email,
                name=demo.name,
                role=demo.role,
                password_hash=hash_password(password),
            )
        )
        created.append(demo.email)
    await session.commit()
    return created


async def _main() -> None:
    settings = get_settings()
    db = Database(settings.database_url, settings.db_connect_timeout_seconds)
    try:
        async with db.sessionmaker() as session:
            created = await seed_demo_users(session, settings)
    finally:
        await db.dispose()

    for demo in DEMO_USERS.values():
        state = "created" if demo.email in created else "already existed"
        print(f"{demo.role.value:<9} {demo.email:<22} {state}")
    if not settings.demo_login_enabled:
        print("Note: DEMO_LOGIN_ENABLED is not true, so one-click demo login is off.")


def main() -> None:
    # psycopg's async mode can't use Windows' default Proactor event loop.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(_main(), loop_factory=loop_factory)


if __name__ == "__main__":
    main()
