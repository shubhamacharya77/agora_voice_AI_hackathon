"""HR specialist: the employee directory, leave balances and leave requests (PostgreSQL)."""

from datetime import date, timedelta
from typing import Any, Self

from pydantic import BaseModel, Field, model_validator
from sqlmodel import col, or_, select

from app.agents.tools import (
    Specialist,
    ToolContext,
    ToolError,
    ToolOutput,
    ToolSpec,
    awaiting_approval,
)
from app.auth.permissions import Permission
from app.models import Employee


class NoInput(BaseModel):
    pass


class FindEmployeeInput(BaseModel):
    name: str | None = Field(default=None, description="Full or partial name")
    department: str | None = Field(
        default=None,
        description="Department, e.g. Engineering, Operations, Finance, People, IT and Security, "
        "Product and Design, Sales and Customer Success, Leadership",
    )

    @model_validator(mode="after")
    def _something_to_look_for(self) -> Self:
        if not (self.name or "").strip() and not (self.department or "").strip():
            raise ValueError("give a name or a department")
        return self


class LeaveRequestInput(BaseModel):
    start_date: date = Field(description="First day of leave (YYYY-MM-DD)")
    end_date: date = Field(description="Last day of leave (YYYY-MM-DD)")
    reason: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def _end_not_before_start(self) -> Self:
        if self.end_date < self.start_date:
            raise ValueError("end_date is before start_date")
        return self


def working_days(start: date, end: date) -> int:
    return sum(1 for i in range((end - start).days + 1) if (start + timedelta(days=i)).weekday() < 5)


def public(employee: Employee) -> dict[str, Any]:
    return {
        "employee_id": employee.emp_code,
        "name": employee.name,
        "title": employee.title,
        "department": employee.department,
        "manager": employee.manager,
        "email": employee.email,
        "office": employee.office,
        "extension": employee.phone_ext,
        "joined_on": employee.joined_on.isoformat(),
        "annual_leave_days_left": employee.annual_leave_balance,
        "sick_leave_days_left": employee.sick_leave_balance,
    }


async def employee_for(ctx: ToolContext) -> Employee:
    employee = (await ctx.session.exec(select(Employee).where(Employee.email == ctx.user.email))).first()
    if employee is None:
        raise ToolError("There's no employee record for this user.")
    return employee


async def my_profile(ctx: ToolContext, args: NoInput) -> ToolOutput:
    me = public(await employee_for(ctx))
    return ToolOutput(data={"employee": me}, card={"type": "employee", "item": me})


async def find_employee(ctx: ToolContext, args: FindEmployeeInput) -> ToolOutput:
    query = select(Employee).order_by(Employee.emp_code)
    if name := (args.name or "").strip():
        query = query.where(col(Employee.name).ilike(f"%{_like(name)}%"))
    if department := (args.department or "").strip():
        pattern = f"%{_like(department)}%"
        query = query.where(or_(col(Employee.department).ilike(pattern), col(Employee.title).ilike(pattern)))
    matches = [public(e) for e in (await ctx.session.exec(query)).all()]
    return ToolOutput(data={"employees": matches, "found": len(matches)}, card={"type": "employees", "items": matches})


def _like(text: str) -> str:
    """Treat % and _ in what the user said as plain characters, not wildcards."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def request_leave(ctx: ToolContext, args: LeaveRequestInput) -> ToolOutput:
    employee = await employee_for(ctx)
    days = working_days(args.start_date, args.end_date)
    action = await ctx.propose(
        action_type="leave.request",
        description=(
            f"Request leave from {args.start_date:%a %d %b} to {args.end_date:%a %d %b %Y} "
            f"({days} working days)"
        ),
        proposal={
            "employee_email": ctx.user.email,
            "start_date": args.start_date.isoformat(),
            "end_date": args.end_date.isoformat(),
            "working_days": days,
            "reason": args.reason,
            "leave_balance_days": employee.annual_leave_balance,
            "exceeds_balance": days > employee.annual_leave_balance,
        },
    )
    return awaiting_approval(action)


HR = Specialist(
    name="hr",
    description="The employee directory, leave balances, leave requests and HR questions.",
    tools=(
        ToolSpec(
            name="hr_get_my_profile",
            description="Get the current user's own employee record, including leave balances.",
            input_model=NoInput,
            permission=Permission.HR_READ_OWN,
            handler=my_profile,
            mock=False,
        ),
        ToolSpec(
            name="hr_find_employee",
            description="Look up employees by name, or list everyone in a department (admins only).",
            input_model=FindEmployeeInput,
            permission=Permission.HR_READ_ALL,
            handler=find_employee,
            mock=False,
        ),
        ToolSpec(
            name="hr_request_leave",
            description="Propose a leave request for the current user. Creates a proposal "
            "that waits for the user's approval; it is not submitted yet.",
            input_model=LeaveRequestInput,
            permission=Permission.LEAVE_REQUEST,
            handler=request_leave,
            consequential=True,
        ),
    ),
)
