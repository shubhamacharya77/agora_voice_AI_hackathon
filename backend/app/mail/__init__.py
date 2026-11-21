"""The admin mailbox: real Gmail, or demo mail when Gmail isn't connected."""

from app.mail.errors import MailError, MailNotConnected, MailUnsure
from app.mail.mailboxes import DemoMailbox, GmailMailbox, Mailbox, configure, mailbox

__all__ = [
    "DemoMailbox",
    "GmailMailbox",
    "MailError",
    "MailNotConnected",
    "MailUnsure",
    "Mailbox",
    "configure",
    "mailbox",
]
