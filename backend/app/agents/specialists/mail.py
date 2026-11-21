"""Mail specialist: the one admin mailbox. Real Gmail once connected, demo mail until then."""

from dataclasses import replace

from pydantic import BaseModel, Field

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
from app.mail import MailError, Mailbox, mailbox


class SearchInboxInput(BaseModel):
    query: str | None = Field(
        default=None,
        description="Words to find in sender, subject or body. Gmail search operators work, "
        "e.g. from:akshay, is:unread, newer_than:2d",
    )
    important_only: bool = Field(default=False, description="Only emails marked important")


class ReadEmailInput(BaseModel):
    email_id: str = Field(description="The id from a search result")


class SendEmailInput(BaseModel):
    to: list[str] = Field(min_length=1, description="Recipient names or email addresses")
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=5000)


async def search_inbox(ctx: ToolContext, args: SearchInboxInput) -> ToolOutput:
    box = mailbox()
    try:
        items = await box.search(args.query, args.important_only)
    except MailError as exc:
        raise ToolError(str(exc)) from exc
    return ToolOutput(
        data={"emails": items, "found": len(items)},
        card={"type": "emails", "items": items, "via": box.via},
        mock=not box.real,
    )


async def read_email(ctx: ToolContext, args: ReadEmailInput) -> ToolOutput:
    box = mailbox()
    try:
        email = await box.read(args.email_id)
    except MailError as exc:
        raise ToolError(str(exc)) from exc
    return ToolOutput(data={"email": email}, card={"type": "email", "item": email}, mock=not box.real)


async def _resolve(box: Mailbox, recipient: str) -> tuple[str, str]:
    """(name, address) for a name or address; ask the user when unsure."""
    if "@" in recipient:
        address = recipient.strip().lower()
        if not mock_data.EMAIL_ADDRESS.match(address):
            raise ToolError(f"'{recipient}' isn't a valid email address.")
        return address, address
    try:
        matches = await box.find_people(recipient)
    except MailError as exc:
        raise ToolError(f"Couldn't look up '{recipient}': {exc}") from exc
    if not matches:
        raise ToolError(f"No email address found for '{recipient}'. Ask the user for it.")
    if len(matches) > 1:
        names = ", ".join(f"{m['name']} ({m['email']})" for m in matches[:4])
        raise ToolError(f"'{recipient}' could be {names}. Ask the user which one.")
    return matches[0]["name"], matches[0]["email"]


async def send_email(ctx: ToolContext, args: SendEmailInput) -> ToolOutput:
    box = mailbox()
    recipients = [await _resolve(box, r) for r in args.to]
    names = ", ".join(f"{name} ({address})" if name != address else address for name, address in recipients)
    action = await ctx.propose(
        action_type="email.send",
        description=f"Send email to {names}. Subject: {args.subject}",
        proposal={
            "to": [address for _, address in recipients],
            "to_names": [name for name, _ in recipients],
            "subject": " ".join(args.subject.split()),  # one line: no header injection
            "body": args.body,
            "from": box.address,
            "via": box.via,  # the mailbox it was drafted for; only that one may send it
        },
    )
    return replace(awaiting_approval(action), mock=not box.real)


MAIL = Specialist(
    name="mail",
    description="The company admin mailbox: search, read, summarize, draft and send email.",
    tools=(
        ToolSpec(
            name="mail_search_inbox",
            description="Search the admin inbox, newest first. Returns sender, subject, time "
            "and a snippet for up to five emails.",
            input_model=SearchInboxInput,
            permission=Permission.EMAIL_READ,
            handler=search_inbox,
        ),
        ToolSpec(
            name="mail_read_email",
            description="Read one email in full.",
            input_model=ReadEmailInput,
            permission=Permission.EMAIL_READ,
            handler=read_email,
        ),
        ToolSpec(
            name="mail_send_email",
            description="Draft an email for sending. Creates a proposal that waits for the "
            "user's approval; nothing is sent yet. Recipients can be names or addresses.",
            input_model=SendEmailInput,
            permission=Permission.EMAIL_SEND,
            handler=send_email,
            consequential=True,
        ),
    ),
)
