"""What actually runs after the user approves: email through the admin Gmail account,
leave against the employee's balance in PostgreSQL, and meetings on demo data.

Each executor returns the provider's own response, and `verify` decides whether that
response really confirms success. Only approvals.execute() calls these.

"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlmodel import col, update
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.permissions import Permission
from app.mail import MailError, MailUnsure, mailbox
from app.models import Employee


class ProviderRejected(Exception):
    """The provider answered and refused: the action did not happen."""


class ProviderTimeout(Exception):
    """No answer in time: the action may or may not have happened."""


@dataclass(frozen=True)
class Executor:
    action_type: str
    permission: Permission
    # (proposal, idempotency key, the caller's database session) -> the provider's response.
    # Database changes made through the session are committed together with the outcome.
    run: Callable[[dict[str, Any], uuid.UUID, AsyncSession | None], Awaitable[dict[str, Any]]]
    verify: Callable[[dict[str, Any]], bool]


async def _send_email(proposal: dict[str, Any], key: uuid.UUID, session: AsyncSession | None = None) -> dict[str, Any]:
    box = mailbox()
    drafted_for = proposal.get("via", "demo")
    if drafted_for != box.via:
        # e.g. drafted for another mailbox (an older demo draft): never send that for real.
        raise ProviderRejected(
            f"This draft was made for the {drafted_for} mailbox, but CoO now uses the {box.via} one. "
            "Ask CoO to draft it again."
        )
    try:
        return await box.send(proposal["to"], proposal["subject"], proposal["body"], key)
    except MailUnsure as exc:
        raise ProviderTimeout(str(exc)) from exc
    except MailError as exc:
        raise ProviderRejected(str(exc)) from exc


async def _cancel_meeting(proposal: dict[str, Any], key: uuid.UUID, session: AsyncSession | None = None) -> dict[str, Any]:
    meeting = proposal["meeting"]
    return {"status": "cancelled", "meeting_id": meeting["id"], "notified": meeting["attendees"], "mock": True}


async def _submit_leave(proposal: dict[str, Any], key: uuid.UUID, session: AsyncSession | None = None) -> dict[str, Any]:
    if session is None:
        raise ProviderRejected("Leave can't be recorded right now.")
    days = int(proposal["working_days"])
    # One atomic step: only deducts when enough days are left, so two approvals can't overdraw.
    deduct = (
        update(Employee)
        .where(col(Employee.email) == proposal["employee_email"], col(Employee.annual_leave_balance) >= days)
        .values(annual_leave_balance=Employee.annual_leave_balance - days)
        .returning(col(Employee.annual_leave_balance))
        .execution_options(synchronize_session=False)
    )
    row = (await session.exec(deduct)).first()
    if row is None:
        raise ProviderRejected(
            f"Not enough annual leave: this needs {days} working days, "
            f"and only {proposal.get('leave_balance_days', 'fewer')} are left."
        )
    return {
        "status": "submitted",
        "request_id": f"LV-{key.hex[:6].upper()}",
        "working_days": days,
        "annual_leave_days_left": row[0],
    }


EXECUTORS: dict[str, Executor] = {
    executor.action_type: executor
    for executor in (
        Executor(
            "email.send",
            Permission.EMAIL_SEND,
            _send_email,
            lambda r: r.get("status") == "sent" and bool(r.get("message_id")),
        ),
        Executor(
            "meeting.cancel",
            Permission.MEETINGS_MANAGE,
            _cancel_meeting,
            lambda r: r.get("status") == "cancelled" and bool(r.get("meeting_id")),
        ),
        Executor(
            "leave.request",
            Permission.LEAVE_REQUEST,
            _submit_leave,
            lambda r: r.get("status") == "submitted" and bool(r.get("request_id")),
        ),
    )
}
