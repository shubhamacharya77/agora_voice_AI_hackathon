"""HR specialist: employee records, leave balances and leave requests."""

from datetime import date
from typing import Self

from pydantic import BaseModel, Field, model_validator

from app.agents import mock_data
from app.agents.tools import (
    Specialist,
    ToolContext,
    ToolError,
    ToolOutput,
    ToolSpec,
    awaiting_approval,
)
from app.auth.permissions import Permission


class NoInput(BaseModel):
    pass


class FindEmployeeInput(BaseModel):
    name: str = Field(min_length=2, description="Full or partial name")


class LeaveRequestInput(BaseModel):
    start_date: date = Field(description="First day of leave (YYYY-MM-DD)")
    end_date: date = Field(description="Last day of leave (YYYY-MM-DD)")
    reason: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def _end_not_before_start(self) -> Self:
        if self.end_date < self.start_date:
            raise ValueError("end_date is before start_date")
        return self


def _public(employee: dict) -> dict:
    return {k: employee[k] for k in ("name", "title", "department", "manager", "email", "leave_balance_days")}


async def my_profile(ctx: ToolContext, args: NoInput) -> ToolOutput:
    employee = mock_data.employee_by_email(ctx.user.email)
    if employee is None:
        raise ToolError("There's no employee record for this user.")
    return ToolOutput(data={"employee": _public(employee)}, card={"type": "employee", "item": _public(employee)})


async def find_employee(ctx: ToolContext, args: FindEmployeeInput) -> ToolOutput:
    matches = [_public(e) for e in mock_data.find_employees(args.name)]
    return ToolOutput(data={"employees": matches, "found": len(matches)})


async def request_leave(ctx: ToolContext, args: LeaveRequestInput) -> ToolOutput:
    employee = mock_data.employee_by_email(ctx.user.email)
    if employee is None:
        raise ToolError("There's no employee record for this user.")
    days = mock_data.working_days(args.start_date, args.end_date)
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
            "leave_balance_days": employee["leave_balance_days"],
            "exceeds_balance": days > employee["leave_balance_days"],
        },
    )
    return awaiting_approval(action)


HR = Specialist(
    name="hr",
    description="Employee records, leave balances, leave requests and HR questions.",
    tools=(
        ToolSpec(
            name="hr_get_my_profile",
            description="Get the current user's own employee record, including leave balance.",
            input_model=NoInput,
            permission=Permission.HR_READ_OWN,
            handler=my_profile,
        ),
        ToolSpec(
            name="hr_find_employee",
            description="Look up any employee's record by name (admins only).",
            input_model=FindEmployeeInput,
            permission=Permission.HR_READ_ALL,
            handler=find_employee,
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
