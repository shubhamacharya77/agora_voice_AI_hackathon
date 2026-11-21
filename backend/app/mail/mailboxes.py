"""The admin mailbox CoO works with: the real Gmail account.

`configure(settings)` picks it at startup. There is no demo inbox: until the Gmail
sign-in is in .env, every mail tool says Gmail isn't connected.
"""

import uuid
from email.utils import getaddresses
from typing import Any, Protocol

from app.agents import mock_data
from app.config import Settings, secret
from app.mail.errors import MailNotConnected
from app.mail.gmail import GmailClient, name_matches

SEARCH_LIMIT = 5


class Mailbox(Protocol):
    connected: bool  # False: every call raises MailNotConnected
    real: bool  # False: sample data, and nothing is really sent (test doubles only)
    via: str  # which mailbox, recorded on every draft: "gmail" (or a test double's name)
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


class GmailMailbox:
    """The real admin Gmail account."""

    connected = True
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


NOT_CONNECTED = "Gmail isn't connected yet. Connect it: uv run python -m app.mail.connect"


class NotConnectedMailbox:
    """No Gmail sign-in in .env: nothing to read, nothing can be sent."""

    connected = False
    real = True
    via = "none"
    address = None

    async def search(self, query: str | None, important_only: bool) -> list[dict[str, Any]]:
        raise MailNotConnected(NOT_CONNECTED)

    async def read(self, email_id: str) -> dict[str, Any]:
        raise MailNotConnected(NOT_CONNECTED)

    async def find_people(self, name: str) -> list[dict[str, str]]:
        raise MailNotConnected(NOT_CONNECTED)

    async def send(self, to: list[str], subject: str, body: str, key: uuid.UUID) -> dict[str, Any]:
        raise MailNotConnected(NOT_CONNECTED)


_current: Mailbox = NotConnectedMailbox()


def mailbox() -> Mailbox:
    return _current


def configure(settings: Settings) -> Mailbox:
    global _current
    _current = GmailMailbox.from_settings(settings) if settings.mail_connected else NotConnectedMailbox()
    return _current
