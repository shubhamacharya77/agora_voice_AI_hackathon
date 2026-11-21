"""Gmail for the one admin mailbox: Google's REST API with an OAuth refresh token.

The refresh token comes from `python -m app.mail.connect` (one-time sign-in). Access
tokens are fetched from it on demand and kept in memory only; nothing here logs them.
"""

import asyncio
import base64
import html
import re
import time
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import getaddresses
from typing import Any

import httpx2

from app.mail.errors import MailError, MailNotConnected, MailUnsure

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
# Least privilege: read the inbox and send. No delete, no settings, no drafts folder.
SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
)
RECONNECT = "Reconnect it: uv run python -m app.mail.connect"
# Email bodies go to the model; keep them short (the free Groq tier counts every token).
BODY_LIMIT = 2000
SNIPPET_LIMIT = 160
HEADERS = ["From", "To", "Cc", "Subject", "Date"]


class GmailClient:
    """Gmail's REST API. `transport` lets tests stand in for Google."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._transport = transport
        self._timeout = timeout
        self._token: str | None = None
        self._token_expires = 0.0

    # --- plumbing ---

    def _client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(timeout=self._timeout, transport=self._transport)

    async def _access_token(self, client: httpx2.AsyncClient) -> str:
        if self._token and time.monotonic() < self._token_expires:
            return self._token
        try:
            response = await client.post(
                TOKEN_URL,
                data={
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "refresh_token": self._refresh_token,
                    "grant_type": "refresh_token",
                },
            )
        except httpx2.HTTPError as exc:
            raise MailError(f"Couldn't reach Google's sign-in service ({type(exc).__name__}).") from exc
        if response.status_code in (400, 401):
            raise MailNotConnected(f"The Gmail sign-in has expired or was revoked. {RECONNECT}")
        if response.status_code != 200:
            raise MailError(f"Google's sign-in service answered {response.status_code}.")
        data = response.json()
        self._token = data["access_token"]
        self._token_expires = time.monotonic() + float(data.get("expires_in", 3600)) - 60
        return self._token

    async def _call(
        self,
        client: httpx2.AsyncClient,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        for attempt in (1, 2):
            token = await self._access_token(client)
            try:
                response = await client.request(
                    method,
                    f"{API}{path}",
                    params=params,
                    json=body,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except (httpx2.ConnectError, httpx2.ConnectTimeout) as exc:
                # Never connected, so nothing reached Gmail.
                raise MailError(f"Couldn't reach Gmail ({type(exc).__name__}).") from exc
            except httpx2.HTTPError as exc:
                raise MailUnsure(f"Gmail didn't answer in time ({type(exc).__name__}).") from exc
            if response.status_code == 401 and attempt == 1:
                self._token = None  # the access token was revoked early: fetch a new one once
                continue
            break
        if response.status_code < 400:
            return response.json() if response.content else {}
        reason = _error_message(response)
        if response.status_code == 401:
            raise MailNotConnected(f"Gmail rejected the sign-in. {RECONNECT}")
        if response.status_code == 403:
            raise MailError(f"Gmail refused: {reason}")
        if response.status_code == 404:
            raise MailError("Gmail has no such email.")
        if response.status_code == 429:
            raise MailError("Gmail is limiting requests right now. Try again in a minute.")
        if response.status_code >= 500:
            raise MailUnsure(f"Gmail had a problem ({response.status_code}).")
        raise MailError(f"Gmail refused: {reason}")

    async def _messages(self, client: httpx2.AsyncClient, query: str, limit: int) -> list[dict[str, Any]]:
        listing = await self._call(client, "GET", "/messages", params={"q": query, "maxResults": limit})
        ids = [m["id"] for m in listing.get("messages", [])][:limit]
        return list(
            await asyncio.gather(
                *(
                    self._call(
                        client,
                        "GET",
                        f"/messages/{message_id}",
                        params={"format": "metadata", "metadataHeaders": HEADERS},
                    )
                    for message_id in ids
                )
            )
        )

    # --- what CoO uses ---

    async def profile(self) -> dict[str, Any]:
        async with self._client() as client:
            return await self._call(client, "GET", "/profile")

    async def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Newest first, as short summaries (sender, subject, time, snippet)."""
        async with self._client() as client:
            return [summary(m) for m in await self._messages(client, query, limit)]

    async def read(self, message_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", message_id):
            raise MailError("Gmail has no such email.")
        async with self._client() as client:
            message = await self._call(client, "GET", f"/messages/{message_id}", params={"format": "full"})
        return {**summary(message), "to": _headers(message).get("to", ""), "body": body_text(message)}

    async def people(self, name: str, *, own_address: str | None = None, limit: int = 10) -> list[dict[str, str]]:
        """People the mailbox has exchanged email with whose name matches."""
        clean = re.sub(r'["\\():<>]', " ", name).strip()
        if not clean:
            return []
        query = f'from:"{clean}" OR to:"{clean}" OR cc:"{clean}"'
        async with self._client() as client:
            messages = await self._messages(client, query, limit)
        found: dict[str, str] = {}
        own = (own_address or "").lower()
        for message in messages:
            headers = _headers(message)
            pairs = getaddresses([headers.get("from", ""), headers.get("to", ""), headers.get("cc", "")])
            for display, address in pairs:
                address = address.strip().lower()
                if address and address != own and name_matches(clean, display, address):
                    found.setdefault(address, display.strip() or address)
        return [{"name": display, "email": address} for address, display in found.items()]

    async def send(
        self, *, sender: str | None, to: list[str], subject: str, body: str, key: str
    ) -> dict[str, Any]:
        raw = build_raw(sender=sender, to=to, subject=subject, body=body, key=key)
        async with self._client() as client:
            sent = await self._call(client, "POST", "/messages/send", body={"raw": raw})
        return {
            "status": "sent",
            "message_id": sent.get("id"),
            "thread_id": sent.get("threadId"),
            "to": to,
            "via": "gmail",
            "mock": False,
        }


# --- message helpers (pure, tested directly) ---


def _error_message(response: httpx2.Response) -> str:
    try:
        error = response.json().get("error", {})
    except ValueError:
        return f"HTTP {response.status_code}"
    if isinstance(error, dict):
        return str(error.get("message") or error.get("status") or response.status_code)
    return str(error)


def _headers(message: dict[str, Any]) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])}


def _received(message: dict[str, Any]) -> str:
    try:
        return datetime.fromtimestamp(int(message["internalDate"]) / 1000, UTC).isoformat()
    except (KeyError, ValueError):
        return _headers(message).get("date", "")


def summary(message: dict[str, Any]) -> dict[str, Any]:
    headers = _headers(message)
    labels = message.get("labelIds", [])
    return {
        "id": message["id"],
        "from": headers.get("from", ""),
        "subject": headers.get("subject") or "(no subject)",
        "received": _received(message),
        "important": "IMPORTANT" in labels,
        "unread": "UNREAD" in labels,
        "snippet": html.unescape(message.get("snippet", ""))[:SNIPPET_LIMIT],
    }


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def _parts(part: dict[str, Any]) -> list[dict[str, Any]]:
    return [part, *(p for child in part.get("parts", []) for p in _parts(child))]


def body_text(message: dict[str, Any]) -> str:
    """The email's text: the plain-text part, or the HTML part without tags."""
    parts = _parts(message.get("payload", {}))
    for kind in ("text/plain", "text/html"):
        for part in parts:
            data = part.get("body", {}).get("data")
            if part.get("mimeType") == kind and data:
                text = _decode(data)
                if kind == "text/html":
                    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
                    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
                text = re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", text)).strip()
                return text[:BODY_LIMIT] + ("…" if len(text) > BODY_LIMIT else "")
    return html.unescape(message.get("snippet", ""))


def build_raw(*, sender: str | None, to: list[str], subject: str, body: str, key: str) -> str:
    """The email as Gmail's `raw` field: RFC 5322, base64url."""
    message = EmailMessage()  # its default policy refuses line breaks in headers
    if sender:
        message["From"] = sender
    message["To"] = ", ".join(to)
    message["Subject"] = subject
    message["X-CoO-Action-Id"] = key  # which approved CoO action sent it
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


def name_matches(name: str, display: str, address: str) -> bool:
    """'Akshay' matches 'Akshay Rao' or akshay.rao@...; 'Akshay Rao' needs both words."""
    wanted = name.lower().split()
    words = set(display.lower().replace(",", " ").split())
    local = set(re.split(r"[._+-]", address.split("@")[0].lower()))
    return all(w in words for w in wanted) or all(w in local for w in wanted)
