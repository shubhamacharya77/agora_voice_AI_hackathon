"""Role-based permissions.

Every protected endpoint and every agent tool checks a Permission, never a role name, so
new roles or finer-grained permissions only change this table. Admin permissions are
listed explicitly; nothing an employee can do is inferred from the admin's set.
"""

from enum import StrEnum

from app.models.user import Role


class Permission(StrEnum):
    # Everyone
    CHAT = "chat"  # talk to CoO and use your own conversations
    WEB_SEARCH = "web:search"
    KNOWLEDGE_READ = "knowledge:read"  # company policies and documents
    HR_READ_OWN = "hr:read_own"  # your own employee record and leave balance
    LEAVE_REQUEST = "leave:request"
    TICKET_CREATE = "tickets:create"  # report IT, facilities or help-desk issues
    # Admin only
    KNOWLEDGE_READ_RESTRICTED = "knowledge:read_restricted"  # admin-only documents
    EMAIL_READ = "email:read"  # the one connected admin mailbox
    EMAIL_SEND = "email:send"
    HR_READ_ALL = "hr:read_all"
    MEETINGS_MANAGE = "meetings:manage"  # schedule, change, cancel
    INVENTORY_MANAGE = "inventory:manage"
    TICKETS_MANAGE = "tickets:manage"


_EMPLOYEE = frozenset(
    {
        Permission.CHAT,
        Permission.WEB_SEARCH,
        Permission.KNOWLEDGE_READ,
        Permission.HR_READ_OWN,
        Permission.LEAVE_REQUEST,
        Permission.TICKET_CREATE,
    }
)
_ADMIN_ONLY = frozenset(
    {
        Permission.KNOWLEDGE_READ_RESTRICTED,
        Permission.EMAIL_READ,
        Permission.EMAIL_SEND,
        Permission.HR_READ_ALL,
        Permission.MEETINGS_MANAGE,
        Permission.INVENTORY_MANAGE,
        Permission.TICKETS_MANAGE,
    }
)

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.EMPLOYEE: _EMPLOYEE,
    Role.ADMIN: _EMPLOYEE | _ADMIN_ONLY,
}


def permissions_for(role: Role) -> frozenset[Permission]:
    return ROLE_PERMISSIONS.get(role, frozenset())


def has_permission(role: Role, permission: Permission) -> bool:
    return permission in permissions_for(role)
