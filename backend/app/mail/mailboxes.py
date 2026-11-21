"""The admin mailbox CoO works with: the real Gmail account, or demo mail.

`configure(settings)` picks one at startup. Gmail is used only when USE_MOCK_GMAIL=false
and the Gmail sign-in is in .env; otherwise CoO uses demo mail and says so.
"""

import uuid
from email.utils import getaddresses
from typing import Any, Protocol

from app.agents import mock_data
from app.config import Settings, secret
from app.mail.errors import MailError, MailUnsure
from app.mail.gmail import GmailClient, name_matches

SEARCH_LIMIT = 5


class Mailbox(Protocol):
    real: bool  # False: demo data, and nothing is really sent
    via: str  # "gmail" or "demo", recorded on every draft
    address: str | None

    async def search(self, query: str | None, important_only: bool) -> list[dict[str, Any]]: ...
    async def read(self, email_id: str) -> dict[str, Any]: ...
    async def find_people(self, name: str) -> list[dict[str, str]]: ...
    async def send(self, to: list[str], subject: str, body: str, key: uuid.UUID) -> dict[str, Any]: ...


def parse_contacts(text: str) -> list[dict[str, str]]:
    """MAIL_CONTACTS: 'Akshay Rao <akshay@example.com>, Priya Nair <priya@example.com>'."""
    return [
        {"name": name.strip() or address.strip(), "email": address.strip().lower()}
        for name, address in getaddresses([text])
        if mock_data.EMAIL_ADDRESS.match(address.strip())
    ]


def _listed(contacts: list[dict[str, str]], name: str) -> list[dict[str, str]]:
    return [c for c in contacts if name_matches(name, c["name"], c["email"])]


class DemoMailbox:
    """Sample inbox and contacts. Demo failure switches: bounce@ is rejected, slow@ times out."""

    real = False
    via = "demo"
    address = "admin@alrium.example"

    async def search(self, query: str | None, important_only: bool) -> list[dict[str, Any]]:
        needle = (query or "").lower()
        return [
            {k: e[k] for k in ("id", "from", "subject", "received", "important")} | {"snippet": e["body"][:120]}
            for e in mock_data.inbox()
            if (not important_only or e["important"])
            and (not needle or needle in f"{e['from']} {e['subject']} {e['body']}".lower())
        ]

    async def read(self, email_id: str) -> dict[str, Any]:
        email = next((e for e in mock_data.inbox() if e["id"] == email_id), None)
        if email is None:
            raise MailError(f"No email with id {email_id}.")
        return email

    async def find_people(self, name: str) -> list[dict[str, str]]:
        return mock_data.find_contacts(name)

    async def send(self, to: list[str], subject: str, body: str, key: uuid.UUID) -> dict[str, Any]:
        if any(address.startswith("bounce@") for address in to):
            raise MailError("The mail server rejected it because the mailbox doesn't exist.")
        if any(address.startswith("slow@") for address in to):
            raise MailUnsure("The mail service didn't answer in time.")
        return {"status": "sent", "message_id": f"mock-{key.hex[:12]}", "to": to, "via": "demo", "mock": True}


class GmailMailbox:
    """The real admin Gmail account."""

    real = True
    via = "gmail"

    def __init__(self, client: GmailClient, address: str | None, contacts: list[dict[str, str]]) -> None:
        self.client = client
        self.address = address
        self.contacts = contacts

    @classmethod
    def from_settings(cls, settings: Settings) -> "GmailMailbox":
        client = GmailClient(
            settings.gmail_client_id or "",
            secret(settings.gmail_client_secret),
            secret(settings.gmail_refresh_token),
        )
        return cls(client, settings.gmail_address, parse_contacts(settings.mail_contacts))

    async def search(self, query: str | None, important_only: bool) -> list[dict[str, Any]]:
        terms = ["in:inbox", "is:important" if important_only else "", (query or "").strip()]
        return await self.client.search(" ".join(t for t in terms if t), limit=SEARCH_LIMIT)

    async def read(self, email_id: str) -> dict[str, Any]:
        return await self.client.read(email_id)

    async def find_people(self, name: str) -> list[dict[str, str]]:
        # The address book in .env wins; otherwise, people this mailbox has emailed with.
        return _listed(self.contacts, name) or await self.client.people(name, own_address=self.address)

    async def send(self, to: list[str], subject: str, body: str, key: uuid.UUID) -> dict[str, Any]:
        return await self.client.send(sender=self.address, to=to, subject=subject, body=body, key=str(key))


_current: Mailbox = DemoMailbox()


def mailbox() -> Mailbox:
    return _current


def configure(settings: Settings) -> Mailbox:
    global _current
    _current = GmailMailbox.from_settings(settings) if settings.mail_mode == "gmail" else DemoMailbox()
    return _current
