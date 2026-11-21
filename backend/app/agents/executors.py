"""What actually runs after the user approves: email through the admin mailbox (real
Gmail once connected), meetings and leave on demo data.

Each executor returns the provider's own response, and `verify` decides whether that
response really confirms success. Only approvals.execute() calls these.

Demo failure switches (demo mail only): an email to bounce@... is rejected by the "mail
server", and an email to slow@... times out, so failure reporting can be shown live.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.auth.permissions import Permission
from app.mail import MailError, MailUnsure, mailbox


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
    box = mailbox()
    drafted_for = proposal.get("via", "demo")
    if drafted_for != box.via:
        # e.g. drafted with demo contacts, then Gmail was connected: never send that for real.
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
