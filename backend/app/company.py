"""Alrium's demo directory and help-desk tickets, loaded into PostgreSQL by `python -m app.seed`.

Everyone here is invented and matches the knowledge base ("About Alrium"). Each employee
gets a login with their own email: the Operations Manager is the admin (the one who may
use the connected Gmail), everyone else is an employee. Logins use the reserved
`.example` domain, so they can never be real mailboxes.
"""

import secrets
from datetime import date, timedelta

from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.passwords import hash_password
from app.config import Settings
from app.models import Employee, Role, Ticket, TicketCategory, TicketStatus, TicketUrgency, User
from app.models.base import utcnow

ADMIN_EMAIL = "neha@alrium.example"

# (code, name, email, title, department, manager, office, extension, joined, annual, sick)
EMPLOYEES: list[tuple[str, str, str, str, str, str | None, str, str, date, int, int]] = [
    ("EMP-001", "Meera Iyer", "meera@alrium.example", "Chief Executive Officer", "Leadership", None, "Bengaluru", "2001", date(2019, 4, 1), 18, 6),
    ("EMP-002", "Arjun Menon", "arjun@alrium.example", "Chief Technology Officer", "Engineering", "Meera Iyer", "Bengaluru", "2002", date(2019, 4, 1), 12, 5),
    ("EMP-003", "Neha Kulkarni", ADMIN_EMAIL, "Operations Manager", "Operations", "Meera Iyer", "Bengaluru", "2101", date(2021, 6, 14), 14, 7),
    ("EMP-004", "Akshay Rao", "akshay@alrium.example", "DevOps Engineer", "Engineering", "Arjun Menon", "Bengaluru", "2214", date(2022, 1, 10), 8, 4),
    ("EMP-005", "Priya Nair", "priya@alrium.example", "Finance Lead", "Finance", "Meera Iyer", "Bengaluru", "2301", date(2020, 2, 3), 17, 6),
    ("EMP-006", "Kavya Sharma", "kavya@alrium.example", "Head of People", "People", "Meera Iyer", "Bengaluru", "2401", date(2020, 8, 17), 15, 7),
    ("EMP-007", "Rohan Das", "rohan@alrium.example", "IT and Security Lead", "IT and Security", "Arjun Menon", "Bengaluru", "2501", date(2021, 3, 1), 9, 3),
    ("EMP-008", "Sneha Pillai", "sneha@alrium.example", "Senior Product Designer", "Product and Design", "Arjun Menon", "Pune", "3104", date(2023, 5, 22), 11, 7),
    ("EMP-009", "Vikram Singh", "vikram@alrium.example", "Account Executive", "Sales and Customer Success", "Meera Iyer", "Remote", "—", date(2024, 2, 5), 13, 7),
    ("EMP-010", "Farhan Ali", "farhan@alrium.example", "Facilities Executive", "Operations", "Neha Kulkarni", "Bengaluru", "2108", date(2023, 9, 11), 10, 5),
]

# (category, summary, details, location, urgency, status, reporter email, assigned to, days ago, resolution)
TICKETS: list[tuple[TicketCategory, str, str, str, TicketUrgency, TicketStatus, str, str | None, float, str | None]] = [
    (TicketCategory.FACILITIES, "Air conditioning not cooling in the Board room",
     "Room stays above 28°C even on the lowest setting. Q3 budget review is booked there.",
     "Bengaluru, 4th floor, Board room", TicketUrgency.HIGH, TicketStatus.OPEN,
     "priya@alrium.example", "Farhan Ali", 0.2, None),
    (TicketCategory.IT, "VPN drops every 10 minutes when working from home",
     "Disconnects on home broadband; customer demos keep cutting out. Started after the last client update.",
     "Remote", TicketUrgency.HIGH, TicketStatus.IN_PROGRESS,
     "vikram@alrium.example", "Rohan Das", 1.5, None),
    (TicketCategory.FACILITIES, "Water leaking under the pantry sink",
     "Small puddle forms every morning; floor is slippery. Placed a wet-floor sign for now.",
     "Bengaluru, 2nd floor pantry", TicketUrgency.HIGH, TicketStatus.IN_PROGRESS,
     "kavya@alrium.example", "Farhan Ali", 1.0, None),
    (TicketCategory.ACCESS, "Access card doesn't open the 3rd floor door",
     "Card works at the main entrance but the 3rd floor reader shows a red light.",
     "Bengaluru, 3rd floor entrance", TicketUrgency.NORMAL, TicketStatus.OPEN,
     "akshay@alrium.example", "Farhan Ali", 0.8, None),
    (TicketCategory.IT, "Laptop won't charge; USB-C port feels loose",
     "Charges only if the cable is held at an angle. MacBook Pro, asset tag ALR-LT-0412.",
     "Pune office", TicketUrgency.NORMAL, TicketStatus.OPEN,
     "sneha@alrium.example", "Rohan Das", 2.0, None),
    (TicketCategory.IT, "Projector in Room 2B shows 'no signal' over HDMI",
     "Tried two laptops and two cables. Leadership sync is in that room on Monday.",
     "Bengaluru, Room 2B", TicketUrgency.NORMAL, TicketStatus.OPEN,
     "meera@alrium.example", None, 0.5, None),
    (TicketCategory.IT, "Weak Wi-Fi in Pune meeting room 2",
     "Video calls freeze; signal drops to one bar inside the room.",
     "Pune, meeting room 2", TicketUrgency.LOW, TicketStatus.OPEN,
     "sneha@alrium.example", None, 3.0, None),
    (TicketCategory.IT, "Phishing email pretending to be the payroll desk",
     "Asked to 'confirm payroll account' through a link. Nobody clicked it.",
     "Email", TicketUrgency.HIGH, TicketStatus.RESOLVED,
     "priya@alrium.example", "Rohan Das", 4.0,
     "Sender domain blocked, the email removed from all inboxes, and a warning sent to everyone."),
    (TicketCategory.FACILITIES, "First-floor printer keeps jamming",
     "Jams on every double-sided job.",
     "Bengaluru, 1st floor", TicketUrgency.LOW, TicketStatus.RESOLVED,
     "farhan@alrium.example", "Farhan Ali", 6.0,
     "Replaced the worn pickup roller; test prints are fine."),
]


def _password(role: Role, settings: Settings) -> str:
    value = settings.demo_admin_password if role is Role.ADMIN else settings.demo_employee_password
    # No configured password: an unguessable one, so nobody can sign in with it.
    return (value.get_secret_value() if value else "") or secrets.token_urlsafe(24)


async def seed_company(session: AsyncSession, settings: Settings) -> dict[str, int]:
    """Add any missing employees and their logins, and the demo tickets on first run.

    Existing rows are left as they are, so CoO's changes (and password changes) survive
    running the seed again.
    """
    counts = {"employees": 0, "logins": 0, "tickets": 0}
    for code, name, email, title, dept, manager, office, ext, joined, annual, sick in EMPLOYEES:
        if await session.get(Employee, code) is None:
            session.add(Employee(emp_code=code, name=name, email=email, title=title, department=dept,
                                 manager=manager, office=office, phone_ext=ext, joined_on=joined,
                                 annual_leave_balance=annual, sick_leave_balance=sick))  # fmt: skip
            counts["employees"] += 1
        if (await session.exec(select(User).where(User.email == email))).first() is None:
            role = Role.ADMIN if email == ADMIN_EMAIL else Role.EMPLOYEE
            session.add(User(email=email, name=name, role=role, password_hash=hash_password(_password(role, settings))))
            counts["logins"] += 1

    if not (await session.exec(select(func.count()).select_from(Ticket))).one():
        names = {email: name for _, name, email, *_ in EMPLOYEES}
        now = utcnow()
        for category, summary, details, location, urgency, status, reporter, assignee, days_ago, note in TICKETS:
            created = now - timedelta(days=days_ago)
            session.add(Ticket(category=category, summary=summary, details=details, location=location,
                               urgency=urgency, status=status, reported_by_name=names[reporter],
                               reported_by_email=reporter, assigned_to=assignee, resolution_note=note,
                               created_at=created, updated_at=created))  # fmt: skip
            counts["tickets"] += 1
    await session.commit()
    return counts
