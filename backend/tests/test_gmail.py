"""The Gmail adapter and the mailbox switch, against a fake Google (no network)."""

import asyncio
import base64
import email
import json
import uuid
from email import policy
from pathlib import Path

import httpx2
import pytest

from app.agents import executors
from app.mail import MailError, MailNotConnected, MailUnsure
from app.mail import mailboxes as mailbox_module
from app.mail.connect import write_env
from app.mail.gmail import GmailClient, body_text, name_matches
from app.config import Settings
from app.mail.mailboxes import GmailMailbox, NotConnectedMailbox, configure, parse_contacts


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def headers(**values: str) -> list[dict[str, str]]:
    return [{"name": k.replace("_", "-").title(), "value": v} for k, v in values.items()]


MESSAGES = {
    "m1": {
        "id": "m1",
        "labelIds": ["INBOX", "IMPORTANT", "UNREAD"],
        "internalDate": "1795000000000",
        "snippet": "The deployment moves to Friday &amp; tests are failing",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": headers(From="Akshay Rao <akshay.rao@gmail.com>", To="me@gmail.com", Subject="Deploy delay"),
            "parts": [
                {"mimeType": "text/plain", "body": {"data": b64("Hi,\n\nIt moves to Friday.\n\nAkshay")}},
                {"mimeType": "text/html", "body": {"data": b64("<p>Hi</p>")}},
            ],
        },
    },
    "m2": {
        "id": "m2",
        "labelIds": ["INBOX"],
        "internalDate": "1794990000000",
        "snippet": "Budget draft",
        "payload": {
            "mimeType": "text/html",
            "headers": headers(From="priya@corp.com", To="me@gmail.com, Akshay Kumar <ak@corp.com>", Subject=""),
            "body": {"data": b64("<style>p{}</style><p>Draft &amp; numbers</p><script>x()</script>")},
        },
    },
}


class FakeGoogle:
    def __init__(self, *, token_status: int = 200, send_status: int = 200) -> None:
        self.token_status = token_status
        self.send_status = send_status
        self.token_calls = 0
        self.sent: list[dict] = []
        self.queries: list[str] = []

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if request.url.host == "oauth2.googleapis.com":
            self.token_calls += 1
            if self.token_status != 200:
                return httpx2.Response(self.token_status, json={"error": "invalid_grant"})
            return httpx2.Response(200, json={"access_token": "at-1", "expires_in": 3600})
        assert request.headers["authorization"] == "Bearer at-1"
        if path.endswith("/messages/send"):
            if self.send_status != 200:
                return httpx2.Response(self.send_status, json={"error": {"message": "backend error"}})
            self.sent.append(json.loads(request.content))
            return httpx2.Response(200, json={"id": "sent-1", "threadId": "t-1", "labelIds": ["SENT"]})
        if path.endswith("/messages"):
            self.queries.append(request.url.params["q"])
            return httpx2.Response(200, json={"messages": [{"id": "m1"}, {"id": "m2"}]})
        if path.endswith("/profile"):
            return httpx2.Response(200, json={"emailAddress": "me@gmail.com"})
        message_id = path.rsplit("/", 1)[-1]
        if message_id in MESSAGES:
            return httpx2.Response(200, json=MESSAGES[message_id])
        return httpx2.Response(404, json={"error": {"message": "Not Found"}})


def gmail(fake: FakeGoogle) -> GmailClient:
    return GmailClient("cid", "csecret", "rtoken", transport=httpx2.MockTransport(fake.handler))


def test_search_summarizes_newest_emails_and_reuses_the_access_token() -> None:
    fake = FakeGoogle()
    client = gmail(fake)

    first = asyncio.run(client.search("in:inbox is:important", limit=5))
    asyncio.run(client.search("in:inbox", limit=5))

    assert fake.token_calls == 1
    assert fake.queries == ["in:inbox is:important", "in:inbox"]
    assert first[0] == {
        "id": "m1",
        "from": "Akshay Rao <akshay.rao@gmail.com>",
        "subject": "Deploy delay",
        "received": "2026-11-18T11:06:40+00:00",
        "important": True,
        "unread": True,
        "snippet": "The deployment moves to Friday & tests are failing",
    }
    assert first[1]["subject"] == "(no subject)"


def test_reading_prefers_plain_text_and_strips_html() -> None:
    client = gmail(FakeGoogle())

    plain = asyncio.run(client.read("m1"))
    html_only = body_text(MESSAGES["m2"])

    assert plain["body"] == "Hi,\n\nIt moves to Friday.\n\nAkshay"
    assert plain["to"] == "me@gmail.com"
    assert html_only == "Draft & numbers"
    with pytest.raises(MailError):
        asyncio.run(client.read("../../settings"))


def test_send_builds_a_real_email_and_reports_gmails_id() -> None:
    fake = FakeGoogle()
    key = str(uuid.uuid4())

    result = asyncio.run(
        gmail(fake).send(sender="me@gmail.com", to=["akshay.rao@gmail.com"], subject="Deploy", body="Friday.", key=key)
    )

    assert result == {
        "status": "sent", "message_id": "sent-1", "thread_id": "t-1",
        "to": ["akshay.rao@gmail.com"], "via": "gmail", "mock": False,
    }  # fmt: skip
    raw = base64.urlsafe_b64decode(fake.sent[0]["raw"])
    sent = email.message_from_bytes(raw, policy=policy.default)
    assert (sent["To"], sent["From"], sent["Subject"], sent["X-CoO-Action-Id"]) == (
        "akshay.rao@gmail.com", "me@gmail.com", "Deploy", key,
    )  # fmt: skip
    assert sent.get_content().strip() == "Friday."


def test_failures_say_whether_the_email_might_have_gone_out() -> None:
    revoked = gmail(FakeGoogle(token_status=400))
    server_error = gmail(FakeGoogle(send_status=500))
    rejected = gmail(FakeGoogle(send_status=400))

    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("no route", request=request)

    unreachable = GmailClient("c", "s", "r", transport=httpx2.MockTransport(refuse))
    send = {"sender": None, "to": ["a@b.co"], "subject": "s", "body": "b", "key": "k"}

    with pytest.raises(MailNotConnected):
        asyncio.run(revoked.send(**send))
    with pytest.raises(MailUnsure):  # Gmail broke mid-request: maybe sent
        asyncio.run(server_error.send(**send))
    with pytest.raises(MailError) as refused:
        asyncio.run(rejected.send(**send))
    assert not isinstance(refused.value, MailUnsure)  # Gmail said no: not sent
    with pytest.raises(MailError) as offline:
        asyncio.run(unreachable.send(**send))
    assert not isinstance(offline.value, MailUnsure)  # never connected: not sent


def test_people_are_found_among_correspondents() -> None:
    people = asyncio.run(gmail(FakeGoogle()).people("Akshay", own_address="me@gmail.com"))

    assert people == [
        {"name": "Akshay Rao", "email": "akshay.rao@gmail.com"},
        {"name": "Akshay Kumar", "email": "ak@corp.com"},
    ]
    assert name_matches("akshay rao", "", "akshay.rao@gmail.com")
    assert not name_matches("akshay rao", "Akshay Kumar", "ak@corp.com")


def test_listed_contacts_win_over_the_mailbox_lookup() -> None:
    contacts = parse_contacts("Akshay Rao <Akshay@Example.com>, not-an-address, Priya Nair <priya@example.com>")
    box = GmailMailbox(gmail(FakeGoogle()), "me@gmail.com", contacts)

    assert contacts == [
        {"name": "Akshay Rao", "email": "akshay@example.com"},
        {"name": "Priya Nair", "email": "priya@example.com"},
    ]
    assert asyncio.run(box.find_people("akshay")) == [{"name": "Akshay Rao", "email": "akshay@example.com"}]
    assert len(asyncio.run(box.find_people("Meera"))) == 0  # not listed, and no Meera in the fake mailbox


def test_a_demo_draft_is_never_sent_through_real_gmail(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeGoogle()
    monkeypatch.setattr(mailbox_module, "_current", GmailMailbox(gmail(fake), "me@gmail.com", []))
    run = executors.EXECUTORS["email.send"].run
    draft = {"to": ["akshay@alrium.example"], "subject": "s", "body": "b", "via": "demo"}

    with pytest.raises(executors.ProviderRejected, match="draft it again"):
        asyncio.run(run(draft, uuid.uuid4()))
    result = asyncio.run(run({**draft, "to": ["akshay.rao@gmail.com"], "via": "gmail"}, uuid.uuid4()))

    assert fake.sent and result["message_id"] == "sent-1"


def test_without_the_gmail_sign_in_mail_says_it_is_not_connected(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, database_url="postgresql://x", jwt_secret="x" * 32,
                        gmail_client_id="cid", gmail_client_secret="secret", gmail_refresh_token="")  # fmt: skip
    box = configure(settings)

    assert isinstance(box, NotConnectedMailbox) and not settings.mail_connected
    with pytest.raises(MailNotConnected, match="app.mail.connect"):
        asyncio.run(box.search(None, False))
    with pytest.raises(MailNotConnected):
        asyncio.run(box.send(["a@b.co"], "s", "b", uuid.uuid4()))


def test_no_draft_is_made_while_gmail_is_not_connected(monkeypatch: pytest.MonkeyPatch) -> None:
    from test_tools import run

    from app.models import Role

    monkeypatch.setattr(mailbox_module, "_current", NotConnectedMailbox())
    draft = run("mail_send_email", {"to": ["someone@corp.com"], "subject": "Hi", "body": "Hello"}, Role.ADMIN)
    inbox = run("mail_search_inbox", {}, Role.ADMIN)

    assert draft.is_error and "isn't connected" in json.loads(draft.content)["error"]
    assert inbox.is_error and "isn't connected" in json.loads(inbox.content)["error"]


def test_write_env_changes_only_the_given_keys(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("# comment\nGMAIL_REFRESH_TOKEN=\nOTHER=keep\nUSE_MOCK_GMAIL=true\n", encoding="utf-8")

    write_env(env, {"GMAIL_REFRESH_TOKEN": "new", "USE_MOCK_GMAIL": "false", "GMAIL_ADDRESS": "me@gmail.com"})

    assert env.read_text(encoding="utf-8") == (
        "# comment\nGMAIL_REFRESH_TOKEN=new\nOTHER=keep\nUSE_MOCK_GMAIL=false\nGMAIL_ADDRESS=me@gmail.com\n"
    )
