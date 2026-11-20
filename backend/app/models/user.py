import uuid
from enum import StrEnum

from sqlmodel import Field, SQLModel

from app.models.base import TimestampMixin, str_enum_type


class Role(StrEnum):
    ADMIN = "admin"
    EMPLOYEE = "employee"


class UserBase(SQLModel):
    """Fields shared by the table model and the API's public view of a user."""

    email: str = Field(max_length=320, unique=True)
    name: str = Field(max_length=200)
    role: Role = Field(sa_type=str_enum_type(Role))


class User(UserBase, TimestampMixin, table=True):
    # "user" is a reserved word in PostgreSQL.
    __tablename__ = "users"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    # argon2 hash; the plaintext password is never stored.
    password_hash: str = Field(max_length=255)
    is_active: bool = Field(default=True)
