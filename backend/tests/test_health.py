import asyncio

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import Database
from app.main import create_app


class FakeDatabase:
    def __init__(self, reachable: bool) -> None:
        self.reachable = reachable
        self.disposed = False

    async def ping(self) -> bool:
        return self.reachable

    async def dispose(self) -> None:
        self.disposed = True


def test_health_ok_when_database_reachable() -> None:
    db = FakeDatabase(reachable=True)
    with TestClient(create_app(database=db)) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
    assert db.disposed, "lifespan should dispose the database on shutdown"


def test_health_503_when_database_unreachable() -> None:
    with TestClient(create_app(database=FakeDatabase(reachable=False))) as client:
        response = client.get("/health")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unavailable"}


@pytest.mark.integration
def test_health_against_real_postgres() -> None:
    """Hits the docker-compose PostgreSQL. Run `docker compose up -d db` first."""
    settings = get_settings()
    db = Database(settings.database_url, settings.db_connect_timeout_seconds)
    # Same loop the server uses on Windows; psycopg async can't run on the Proactor loop.
    with TestClient(
        create_app(database=db),
        backend_options={"loop_factory": asyncio.SelectorEventLoop},
    ) as client:
        response = client.get("/health")
    assert response.status_code == 200, (
        "PostgreSQL not reachable - is `docker compose up -d db` running?"
    )
    assert response.json() == {"status": "ok", "database": "ok"}
