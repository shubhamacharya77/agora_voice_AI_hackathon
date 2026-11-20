"""Alembic environment.

The database URL comes from app settings (DATABASE_URL), not alembic.ini, so there is a
single source of truth. Migrations run synchronously: psycopg 3 serves the same
`postgresql+psycopg://` URL in sync mode, which avoids event-loop issues on Windows.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlmodel import create_engine

from app.config import get_settings
from app.models import SQLModel

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata

# Tables created and managed by LangGraph's Postgres checkpointer (its own `setup()`).
# Without this filter, autogenerate would propose dropping them.
LANGGRAPH_TABLES = {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}


def include_name(name: str | None, type_: str, parent_names: dict) -> bool:
    if type_ == "table":
        return name not in LANGGRAPH_TABLES
    return True


def database_url() -> str:
    # Tests point Alembic at their own database through config.attributes.
    return config.attributes.get("database_url") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        include_name=include_name,
        compare_type=True,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(database_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_name=include_name,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
