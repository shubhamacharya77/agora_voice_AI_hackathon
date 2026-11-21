import asyncio
import os
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

# Settings need these. Tests use them unless the variables are exported in the shell
# (exported variables take precedence over the repo-root .env, which tests never need).
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://coo:coo@localhost:5433/coo")
os.environ.setdefault("JWT_SECRET", "test-only-jwt-secret-never-used-for-real-0123456789")
# Tests never touch the real mailbox or real web search, whatever .env says
# (exported empty values win over .env). The fakes below stand in for both.
os.environ["GMAIL_REFRESH_TOKEN"] = ""
os.environ["MAIL_CONTACTS"] = ""
os.environ["TAVILY_API_KEY"] = ""
# Tests never send traces to LangSmith.
os.environ["LANGSMITH_TRACING"] = "false"

from alembic import command as alembic_command  # noqa: E402
from alembic.config import Config as AlembicConfig  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import Engine, make_url  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402
from sqlmodel import Session, create_engine, text  # noqa: E402

import app.mail  # noqa: E402
import app.websearch  # noqa: E402
from app.auth.passwords import hash_password  # noqa: E402
from app.auth.tokens import create_access_token  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import Role, User  # noqa: E402
from app.seed import seed_demo_users  # noqa: E402
from app.mail import mailboxes  # noqa: E402
from fakes import FakeMailbox, FakeWeb  # noqa: E402

TEST_DB_NAME = "coo_test"
BACKEND_DIR = Path(__file__).resolve().parents[1]
TEST_PASSWORD = "correct horse battery staple"
TEST_JWT_SECRET = "test-only-jwt-secret-never-used-for-real-0123456789"


@pytest.fixture(autouse=True)
def fake_services(monkeypatch: pytest.MonkeyPatch) -> FakeWeb:
    """Every test gets the sample mailbox and canned web results, even after create_app()."""
    box, web = FakeMailbox(), FakeWeb()
    monkeypatch.setattr(mailboxes, "_current", box)
    monkeypatch.setattr(app.websearch, "_current", web)
    monkeypatch.setattr(app.mail, "configure", lambda settings: box)
    monkeypatch.setattr(app.websearch, "configure", lambda settings: web)
    return web


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """A separate `coo_test` database, rebuilt and migrated to head once per test run."""
    server_url = make_url(get_settings().database_url)
    admin_engine = create_engine(
        server_url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        connect_args={"connect_timeout": 3},
    )
    try:
        with admin_engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": TEST_DB_NAME}
            ).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    except OperationalError as exc:
        pytest.fail(
            f"PostgreSQL isn't reachable for integration tests ({type(exc).__name__}). "
            "Start it with `docker compose up -d db`, or run `pytest -m 'not integration'`."
        )
    finally:
        admin_engine.dispose()

    url = server_url.set(database=TEST_DB_NAME).render_as_string(hide_password=False)
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()

    # Build the schema through the real migrations, so tests also prove they apply cleanly.
    alembic_cfg = AlembicConfig()
    alembic_cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    alembic_cfg.attributes["database_url"] = url
    alembic_command.upgrade(alembic_cfg, "head")
    return url


@pytest.fixture
def test_settings(test_database_url: str) -> Settings:
    return Settings(
        _env_file=None,
        database_url=test_database_url,
        jwt_secret=TEST_JWT_SECRET,
        demo_login_enabled=True,
        demo_admin_password=TEST_PASSWORD,
        demo_employee_password=TEST_PASSWORD,
        summary_every_messages=0,  # scripted models only answer what each test scripts
    )


@pytest.fixture
def sync_engine(test_database_url: str) -> Iterator[Engine]:
    engine = create_engine(test_database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def clean_db(sync_engine: Engine) -> None:
    """Empty every app table before the test (the schema itself stays)."""
    with sync_engine.begin() as conn:
        tables = conn.execute(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                "AND tablename NOT IN ('alembic_version', 'checkpoint_migrations')"
            )
        ).scalars().all()
        if tables:
            names = ", ".join(f'"{name}"' for name in tables)
            conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


@pytest.fixture
def make_user(sync_engine: Engine, clean_db: None) -> Callable[..., User]:
    def _make(
        email: str,
        role: Role,
        *,
        password: str = TEST_PASSWORD,
        name: str = "Test User",
        is_active: bool = True,
    ) -> User:
        with Session(sync_engine, expire_on_commit=False) as session:
            user = User(
                email=email,
                name=name,
                role=role,
                password_hash=hash_password(password),
                is_active=is_active,
            )
            session.add(user)
            session.commit()
            return user

    return _make


@pytest.fixture
def run_db(test_database_url: str, clean_db: None) -> Callable[[Callable[[Database], Awaitable[Any]]], Any]:
    """Run an async function against the test database: `run_db(lambda db: ...)`."""

    def _run(fn: Callable[[Database], Awaitable[Any]]) -> Any:
        async def _go() -> Any:
            db = Database(test_database_url)
            try:
                return await fn(db)
            finally:
                await db.dispose()

        return asyncio.run(_go(), loop_factory=asyncio.SelectorEventLoop)

    return _run


@pytest.fixture
def seed(run_db, test_settings: Settings) -> Callable[[], list[str]]:
    """Run the real demo-user seed against the test database."""

    async def _seed(db: Database) -> list[str]:
        async with db.sessionmaker() as session:
            return await seed_demo_users(session, test_settings)

    return lambda: run_db(_seed)


@pytest.fixture
def auth_headers(test_settings: Settings) -> Callable[[User], dict[str, str]]:
    def _headers(user: User) -> dict[str, str]:
        token, _ = create_access_token(user.id, test_settings)
        return {"Authorization": f"Bearer {token}"}

    return _headers


@pytest.fixture
def client(test_database_url: str, test_settings: Settings, clean_db: None) -> Iterator[TestClient]:
    app = create_app(database=Database(test_database_url))
    app.dependency_overrides[get_settings] = lambda: test_settings
    # psycopg's async mode needs the selector loop on Windows (same as the server).
    with TestClient(app, backend_options={"loop_factory": asyncio.SelectorEventLoop}) as test_client:
        yield test_client
