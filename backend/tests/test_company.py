"""Employees, logins and tickets in PostgreSQL, and the HR and ticket tools that use them."""

import json
import uuid

import pytest
from sqlmodel import select

from app.agents import executors
from app.agents.llm import ToolCall
from app.agents.specialists import build_registry
from app.agents.tools import ToolContext, UserContext
from app.auth.demo import DEMO_USERS
from app.auth.permissions import permissions_for
from app.company import ADMIN_EMAIL, seed_company
from app.models import Employee, Role, Ticket, TicketStatus, User
from app.seed import retire_demo_users

pytestmark = pytest.mark.integration

EMPLOYEE_EMAIL = "akshay@alrium.example"


@pytest.fixture
def company(run_db, test_settings):
    async def _seed(db):
        async with db.sessionmaker() as session:
            return await seed_company(session, test_settings)

    return lambda: run_db(_seed)


def call(run_db, email: str, tool: str, arguments: dict) -> tuple[bool, dict]:
    """Run one tool as the user with this login, committing like a turn does."""

    async def _go(db):
        async with db.sessionmaker() as session:
            user = (await session.exec(select(User).where(User.email == email))).one()
            context = ToolContext(
                user=UserContext(id=user.id, name=user.name, email=user.email, role=user.role,
                                 permissions=permissions_for(user.role)),  # fmt: skip
                conversation_id=uuid.uuid4(),
                session=session,
            )
            result = await build_registry().execute(ToolCall("toolu_1", tool, arguments), context)
            await session.commit()
            return result.is_error, json.loads(result.content)

    return run_db(_go)


def test_seed_loads_ten_employees_their_logins_and_tickets_once(company, run_db) -> None:
    first, second = company(), company()

    assert first == {"employees": 10, "logins": 10, "tickets": 9}
    assert second == {"employees": 0, "logins": 0, "tickets": 0}  # safe to run again

    async def _read(db):
        async with db.sessionmaker() as session:
            employees = (await session.exec(select(Employee))).all()
            users = (await session.exec(select(User))).all()
            return {e.department for e in employees}, {u.email: u.role for u in users}

    departments, roles = run_db(_read)
    assert len(departments) == 8
    assert [email for email, role in roles.items() if role is Role.ADMIN] == [ADMIN_EMAIL]


def test_every_employee_signs_in_with_their_own_email(company, client, test_settings) -> None:
    company()

    employee = client.post("/auth/login", json={"email": EMPLOYEE_EMAIL, "password": "correct horse battery staple"})
    admin = client.post("/auth/login", json={"email": ADMIN_EMAIL, "password": "correct horse battery staple"})

    assert employee.status_code == 200 and employee.json()["user"]["role"] == "employee"
    assert "email:read" not in employee.json()["user"]["permissions"]
    assert admin.status_code == 200 and admin.json()["user"]["name"] == "Neha Kulkarni"
    assert "email:read" in admin.json()["user"]["permissions"]


def test_profile_comes_from_the_database(company, run_db) -> None:
    company()

    error, data = call(run_db, EMPLOYEE_EMAIL, "hr_get_my_profile", {})

    assert not error and "mock" not in data
    assert data["employee"]["name"] == "Akshay Rao"
    assert data["employee"]["department"] == "Engineering"
    assert data["employee"]["annual_leave_days_left"] == 8


def test_admins_look_up_people_by_department_and_employees_cannot(company, run_db) -> None:
    company()

    _, engineering = call(run_db, ADMIN_EMAIL, "hr_find_employee", {"department": "engineering"})
    _, wildcard = call(run_db, ADMIN_EMAIL, "hr_find_employee", {"name": "%"})
    refused, _ = call(run_db, EMPLOYEE_EMAIL, "hr_find_employee", {"name": "Priya"})

    assert {e["name"] for e in engineering["employees"]} == {"Arjun Menon", "Akshay Rao"}
    assert wildcard["found"] == 0  # % is a character, not "everyone"
    assert refused


def test_employees_see_only_their_own_tickets_admins_see_all(company, run_db) -> None:
    company()

    _, mine = call(run_db, "sneha@alrium.example", "tickets_list", {"status": "all"})
    _, everyone = call(run_db, ADMIN_EMAIL, "tickets_list", {})

    assert {t["reported_by"] for t in mine["tickets"]} == {"Sneha Pillai"} and mine["found"] == 2
    assert everyone["found"] == 7  # active = open or in progress
    assert [t["urgency"] for t in everyone["tickets"]][:4] == ["high", "high", "high", "normal"]


def test_a_reported_issue_is_stored_and_an_admin_can_resolve_it(company, run_db) -> None:
    company()

    _, created = call(run_db, EMPLOYEE_EMAIL, "facilities_report_issue",
                      {"summary": "Monitor flickers", "category": "it", "location": "Desk 3-14"})  # fmt: skip
    code = created["ticket"]["id"]
    refused, _ = call(run_db, EMPLOYEE_EMAIL, "tickets_update", {"ticket": code, "status": "resolved"})
    _, updated = call(run_db, ADMIN_EMAIL, "tickets_update",
                      {"ticket": code.lower(), "status": "resolved", "resolution_note": "Swapped the cable"})  # fmt: skip
    missing, _ = call(run_db, ADMIN_EMAIL, "tickets_update", {"ticket": "TCK-99", "status": "resolved"})

    assert code == "TCK-1010" and created["ticket"]["reported_by"] == "Akshay Rao"
    assert refused and missing
    assert updated["ticket"]["status"] == "resolved"
    assert updated["ticket"]["resolution_note"] == "Swapped the cable"


def test_approved_leave_is_deducted_once_and_never_overdrawn(company, run_db) -> None:
    company()
    run = executors.EXECUTORS["leave.request"].run
    proposal = {"employee_email": EMPLOYEE_EMAIL, "working_days": 5, "leave_balance_days": 8}

    async def _go(db):
        async with db.sessionmaker() as session:
            first = await run(proposal, uuid.uuid4(), session)
            with pytest.raises(executors.ProviderRejected, match="Not enough annual leave"):
                await run(proposal, uuid.uuid4(), session)  # only 3 days left now
            await session.commit()
            employee = (await session.exec(select(Employee).where(Employee.email == EMPLOYEE_EMAIL))).one()
            return first, employee.annual_leave_balance

    result, left = run_db(_go)
    assert result["status"] == "submitted" and result["annual_leave_days_left"] == 3
    assert left == 3


def test_old_demo_accounts_are_switched_off(seed, run_db) -> None:
    seed()

    async def _go(db):
        async with db.sessionmaker() as session:
            retired = await retire_demo_users(session)
            again = await retire_demo_users(session)
            users = (await session.exec(select(User))).all()
            return retired, again, {u.email: u.is_active for u in users}

    retired, again, active = run_db(_go)
    assert set(retired) == {d.email for d in DEMO_USERS.values()} and again == []
    assert not any(active.values())


def test_ticket_codes_are_stable(company, run_db) -> None:
    company()

    async def _codes(db):
        async with db.sessionmaker() as session:
            return [t.code for t in (await session.exec(select(Ticket).order_by(Ticket.number))).all()]

    codes = run_db(_codes)
    assert codes[0] == "TCK-1001" and len(codes) == 9
    assert TicketStatus.RESOLVED.value == "resolved"
