import uuid

from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import postgresql
from sqlmodel import Field

from app.models import SQLModel


class _NamingProbe(SQLModel, table=True):
    """Throwaway table used only to check constraint naming; never migrated."""

    __tablename__ = "naming_probe"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    email: str = Field(unique=True)


def test_table_models_use_the_naming_convention() -> None:
    ddl = str(CreateTable(_NamingProbe.__table__).compile(dialect=postgresql.dialect()))
    assert "CONSTRAINT pk_naming_probe PRIMARY KEY" in ddl
    assert "CONSTRAINT uq_naming_probe_email UNIQUE" in ddl


def test_title_from_first_message() -> None:
    from app.conversations.store import title_from

    assert title_from("  What's   the news\n today? ") == "What's the news today?"
    assert title_from("   ") == "New conversation"
    long = title_from("word " * 40)
    assert len(long) == 60
    assert long.endswith("…")
