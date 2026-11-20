"""Mail specialist: the one connected admin mailbox. Mock now; Gmail later."""

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


class SearchInboxInput(BaseModel):
    query: str | None = Field(default=None, description="Words to find in sender, subject or body")
    important_only: bool = Field(default=False, description="Only emails marked important")


class ReadEmailInput(BaseModel):
    email_id: str = Field(description="Id from a search result, e.g. mail-001")


class SendEmailInput(BaseModel):
    to: list[str] = Field(min_length=1, description="Recipient names or email addresses")
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=5000)


def _summary(email: dict) -> dict:
    return {k: email[k] for k in ("id", "from", "subject", "received", "important")} | {
        "snippet": email["body"][:120]
    }


async def search_inbox(ctx: ToolContext, args: SearchInboxInput) -> ToolOutput:
    needle = (args.query or "").lower()
    emails = [
        e
        for e in mock_data.inbox()
        if (not args.important_only or e["important"])
        and (not needle or needle in f"{e['from']} {e['subject']} {e['body']}".lower())
    ]
    items = [_summary(e) for e in emails]
    return ToolOutput(data={"emails": items, "found": len(items)}, card={"type": "emails", "items": items})


async def read_email(ctx: ToolContext, args: ReadEmailInput) -> ToolOutput:
    email = next((e for e in mock_data.inbox() if e["id"] == args.email_id), None)
    if email is None:
        raise ToolError(f"No email with id {args.email_id}.")
    return ToolOutput(data={"email": email}, card={"type": "email", "item": email})


def _resolve(recipient: str) -> tuple[str, str]:
    """(name, address) for a name or address; ask the user when unsure."""
    if "@" in recipient:
        address = recipient.strip().lower()
        if not mock_data.EMAIL_ADDRESS.match(address):
            raise ToolError(f"'{recipient}' isn't a valid email address.")
        return address, address
    matches = mock_data.find_contacts(recipient)
    if not matches:
        raise ToolError(f"No email address on file for '{recipient}'. Ask the user for it.")
    if len(matches) > 1:
        names = ", ".join(m["name"] for m in matches)
        raise ToolError(f"'{recipient}' could be {names}. Ask the user which one.")
    return matches[0]["name"], matches[0]["email"]


async def send_email(ctx: ToolContext, args: SendEmailInput) -> ToolOutput:
    recipients = [_resolve(r) for r in args.to]
    names = ", ".join(f"{name} ({address})" if name != address else address for name, address in recipients)
    action = await ctx.propose(
        action_type="email.send",
        description=f"Send email to {names}. Subject: {args.subject}",
        proposal={
            "to": [address for _, address in recipients],
            "to_names": [name for name, _ in recipients],
            "subject": args.subject,
            "body": args.body,
        },
    )
    return awaiting_approval(action)


MAIL = Specialist(
    name="mail",
    description="The company admin mailbox: search, read, summarize, draft and send email.",
    tools=(
        ToolSpec(
            name="mail_search_inbox",
            description="Search the admin inbox. Returns sender, subject, time and a snippet.",
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
