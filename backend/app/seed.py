"""Load Alrium's employees, their logins, the demo tickets and the knowledge base. Safe to run repeatedly.

Usage (from backend/):  uv run python -m app.seed

Existing employees, logins and tickets are left exactly as they are, so CoO's changes
survive, and changing a DEMO_*_PASSWORD later does not update an existing login. The
knowledge base is replaced each run. The old one-click demo accounts are switched off.
"""

import asyncio
import secrets
import sys

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.demo import DEMO_USERS, demo_password
from app.auth.passwords import hash_password
from app.company import ADMIN_EMAIL, EMPLOYEES, seed_company
from app.config import Settings, get_settings
from app.db import Database
from app.knowledge import store as knowledge
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


async def retire_demo_users(session: AsyncSession) -> list[str]:
    """Switch off the old one-click demo accounts: everyone signs in as a named employee now."""
    retired: list[str] = []
    for demo in DEMO_USERS.values():
        user = (await session.exec(select(User).where(User.email == demo.email))).first()
        if user is not None and user.is_active:
            user.is_active = False
            session.add(user)
            retired.append(demo.email)
    await session.commit()
    return retired


async def _main() -> None:
    settings = get_settings()
    db = Database(settings.database_url, settings.db_connect_timeout_seconds)
    try:
        async with db.sessionmaker() as session:
            counts = await seed_company(session, settings)
            retired = await retire_demo_users(session)
            passages = await knowledge.ingest(session, settings.knowledge_dir)
            await session.commit()
    finally:
        await db.dispose()

    print(f"added: {counts['employees']} employee(s), {counts['logins']} login(s), {counts['tickets']} ticket(s)")
    if retired:
        print(f"switched off the old demo login(s): {', '.join(retired)}")
    print(f"knowledge base: {passages} passages loaded from {settings.knowledge_dir}")
    print()
    print("Logins (passwords: DEMO_ADMIN_PASSWORD / DEMO_EMPLOYEE_PASSWORD in .env):")
    for code, name, email, title, department, *_ in EMPLOYEES:
        role = "admin" if email == ADMIN_EMAIL else "employee"
        print(f"  {role:<9} {email:<24} {name}, {title} ({department})")


def main() -> None:
    # psycopg's async mode can't use Windows' default Proactor event loop.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(_main(), loop_factory=loop_factory)


if __name__ == "__main__":
    main()
