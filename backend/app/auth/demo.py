"""The two demo accounts used for one-click login during the hackathon demo."""

from dataclasses import dataclass

from app.config import Settings
from app.models.user import Role


@dataclass(frozen=True)
class DemoUser:
    role: Role
    email: str
    name: str


# `.example` is a reserved top-level domain (RFC 2606), so these are never real mailboxes.
DEMO_USERS: dict[Role, DemoUser] = {
    Role.ADMIN: DemoUser(Role.ADMIN, "admin@coo.example", "Demo Admin"),
    Role.EMPLOYEE: DemoUser(Role.EMPLOYEE, "employee@coo.example", "Demo Employee"),
}


def demo_password(role: Role, settings: Settings) -> str | None:
    """The configured password for a demo user's email + password login, if any."""
    secret = settings.demo_admin_password if role is Role.ADMIN else settings.demo_employee_password
    if secret is None:
        return None
    return secret.get_secret_value() or None
