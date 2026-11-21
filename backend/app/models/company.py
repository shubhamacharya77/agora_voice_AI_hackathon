"""Company records CoO works with: the employee directory and help-desk tickets.

The rows are invented demo data (loaded by `python -m app.seed`), but they live in
PostgreSQL like real data: tickets CoO logs or updates are stored and stay.
"""

from datetime import date
from enum import StrEnum

from sqlmodel import Column, Field, Identity, Integer, Text

from app.models.base import TimestampMixin, str_enum_type


class Employee(TimestampMixin, table=True):
    """One person in the directory. `email` links them to their login (users.email)."""

    __tablename__ = "employees"

    emp_code: str = Field(max_length=20, primary_key=True)  # e.g. "EMP-001"
    name: str = Field(max_length=200)
    email: str = Field(max_length=320, unique=True)
    title: str = Field(max_length=200)
    department: str = Field(max_length=100, index=True)
    manager: str | None = Field(default=None, max_length=200)  # the manager's name
    office: str = Field(max_length=100)  # "Bengaluru", "Pune" or "Remote"
    phone_ext: str = Field(max_length=20)  # desk extension
    joined_on: date
    annual_leave_balance: int  # days left this year
    sick_leave_balance: int


class TicketCategory(StrEnum):
    IT = "it"
    FACILITIES = "facilities"
    ACCESS = "access"  # access cards, doors, accounts
    OTHER = "other"


class TicketUrgency(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class TicketStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"


class Ticket(TimestampMixin, table=True):
    """An IT, facilities or access issue someone reported."""

    __tablename__ = "tickets"

    number: int | None = Field(default=None, sa_column=Column(Integer, Identity(start=1001), primary_key=True))
    category: TicketCategory = Field(sa_type=str_enum_type(TicketCategory))
    summary: str = Field(max_length=300)
    details: str = Field(default="", sa_type=Text)
    location: str | None = Field(default=None, max_length=120)
    urgency: TicketUrgency = Field(default=TicketUrgency.NORMAL, sa_type=str_enum_type(TicketUrgency))
    status: TicketStatus = Field(default=TicketStatus.OPEN, sa_type=str_enum_type(TicketStatus), index=True)
    reported_by_name: str = Field(max_length=200)
    reported_by_email: str = Field(max_length=320, index=True)
    assigned_to: str | None = Field(default=None, max_length=200)
    resolution_note: str | None = Field(default=None, sa_type=Text)

    @property
    def code(self) -> str:
        return f"TCK-{self.number}"
