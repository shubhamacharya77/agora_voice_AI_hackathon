"""Mail failures. Messages are plain sentences, safe to show and read aloud."""


class MailError(Exception):
    """The mailbox refused or failed. For a send: nothing was sent."""


class MailNotConnected(MailError):
    """Gmail isn't signed in, or the sign-in was revoked or expired."""


class MailUnsure(MailError):
    """No clear answer. For a send: the email may or may not have gone out."""
