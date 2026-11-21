"""What actually runs after the user approves. Mock providers for now; real Gmail later.

Each executor returns the provider's own response, and `verify` decides whether that
response really confirms success. Only approvals.execute() calls these.

Demo failure switches: an email to bounce@... is rejected by the "mail server", and an
email to slow@... times out, so failure reporting can be shown live.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.auth.permissions import Permission


class ProviderRejected(Exception):
    """The provider answered and refused: the action did not happen."""


class ProviderTimeout(Exception):
    """No answer in time: the action may or may not have happened."""


@dataclass(frozen=True)
class Executor:
    action_type: str
    permission: Permission
    # (proposal, idempotency key) -> the provider's response
    run: Callable[[dict[str, Any], uuid.UUID], Awaitable[dict[str, Any]]]
    verify: Callable[[dict[str, Any]], bool]


async def _send_email(proposal: dict[str, Any], key: uuid.UUID) -> dict[str, Any]:
    recipients: list[str] = proposal.get("to", [])
    if any(address.startswith("bounce@") for address in recipients):
        raise ProviderRejected("The mail server rejected it because the mailbox doesn't exist.")
    if any(address.startswith("slow@") for address in recipients):
        raise ProviderTimeout("The mail service didn't answer in time.")
    # A real provider gets `key` so a retried request can't send twice.
    return {"status": "sent", "message_id": f"mock-{key.hex[:12]}", "to": recipients, "mock": True}


async def _cancel_meeting(proposal: dict[str, Any], key: uuid.UUID) -> dict[str, Any]:
    meeting = proposal["meeting"]
    return {"status": "cancelled", "meeting_id": meeting["id"], "notified": meeting["attendees"], "mock": True}


async def _submit_leave(proposal: dict[str, Any], key: uuid.UUID) -> dict[str, Any]:
    return {"status": "submitted", "request_id": f"LV-{key.hex[:6].upper()}", "mock": True}


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
