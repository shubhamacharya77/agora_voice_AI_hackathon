"""The admin mailbox: the real Gmail account (no demo inbox)."""

from app.mail.errors import MailError, MailNotConnected, MailUnsure
from app.mail.mailboxes import GmailMailbox, Mailbox, NotConnectedMailbox, configure, mailbox

__all__ = [
    "GmailMailbox",
    "MailError",
    "MailNotConnected",
    "MailUnsure",
    "Mailbox",
    "NotConnectedMailbox",
    "configure",
    "mailbox",
]
