"""Shared base pieces for all table models.

Every table is a `SQLModel` subclass with `table=True`; they all register on the single
`SQLModel.metadata`, which Alembic reads for autogenerate.
"""

from datetime import UTC, datetime
from enum import StrEnum

from sqlmodel import DateTime, Enum, Field, SQLModel, func

# Stable constraint names so Alembic migrations stay readable and reversible.
# Must be set before any table model is defined, which is why this module is imported first.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}
SQLModel.metadata.naming_convention = NAMING_CONVENTION


def utcnow() -> datetime:
    return datetime.now(UTC)


def str_enum_type(enum_cls: type[StrEnum]) -> Enum:
    """Column type for a StrEnum: stored as VARCHAR holding the values ("admin").

    Not a native Postgres ENUM, so adding a value later is a code change rather than an
    ALTER TYPE migration.
    """
    return Enum(
        enum_cls,
        native_enum=False,
        length=32,
        values_callable=lambda members: [member.value for member in members],
    )


class CreatedAtMixin(SQLModel):
    """Adds a timezone-aware `created_at` column (for rows that never change)."""

    created_at: datetime = Field(
        default_factory=utcnow,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"server_default": func.now()},
    )


class TimestampMixin(CreatedAtMixin):
    """Adds timezone-aware `created_at` and `updated_at` columns."""

    updated_at: datetime = Field(
        default_factory=utcnow,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"server_default": func.now(), "onupdate": func.now()},
    )


__all__ = [
    "NAMING_CONVENTION",
    "CreatedAtMixin",
    "SQLModel",
    "TimestampMixin",
    "str_enum_type",
    "utcnow",
]
