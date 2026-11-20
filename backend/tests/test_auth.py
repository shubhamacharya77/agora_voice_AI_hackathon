"""Login, demo login, current user and permission checks, against the test database."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Depends
from sqlmodel import Session, select

from app.auth.dependencies import require_permission
from app.auth.permissions import Permission
from app.auth.tokens import create_access_token
from app.models import Role, User
from conftest import TEST_PASSWORD

pytestmark = pytest.mark.integration


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_login_returns_token_and_public_user(client, make_user) -> None:
    make_user("ada@coo.example", Role.ADMIN, name="Ada")

    response = client.post(
        "/auth/login", json={"email": "  ADA@coo.example ", "password": TEST_PASSWORD}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0
    assert body["user"]["email"] == "ada@coo.example"
    assert body["user"]["role"] == "admin"
    assert "email:send" in body["user"]["permissions"]
    assert "password_hash" not in body["user"]

    me = client.get("/auth/me", headers=bearer(body["access_token"]))
    assert me.status_code == 200
    assert me.json()["email"] == "ada@coo.example"


def test_wrong_password_and_unknown_email_get_the_same_error(client, make_user) -> None:
    make_user("ben@coo.example", Role.EMPLOYEE)

    wrong_password = client.post(
        "/auth/login", json={"email": "ben@coo.example", "password": "nope"}
    )
    unknown_email = client.post(
        "/auth/login", json={"email": "nobody@coo.example", "password": TEST_PASSWORD}
    )

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()


def test_deactivated_user_cannot_log_in_or_use_an_old_token(client, make_user, test_settings) -> None:
    user = make_user("cy@coo.example", Role.EMPLOYEE, is_active=False)
    old_token, _ = create_access_token(user.id, test_settings)

    login = client.post("/auth/login", json={"email": "cy@coo.example", "password": TEST_PASSWORD})

    assert login.status_code == 401
    assert client.get("/auth/me", headers=bearer(old_token)).status_code == 401


def test_me_rejects_missing_garbage_expired_and_orphaned_tokens(
    client, make_user, test_settings
) -> None:
    user = make_user("dee@coo.example", Role.EMPLOYEE)
    expired, _ = create_access_token(
        user.id, test_settings, now=datetime.now(UTC) - timedelta(days=2)
    )
    orphaned, _ = create_access_token(uuid.uuid4(), test_settings)  # no such user

    missing = client.get("/auth/me")
    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    for token in ("not-a-jwt", expired, orphaned):
        assert client.get("/auth/me", headers=bearer(token)).status_code == 401


def test_demo_login_works_for_each_role_after_seeding(client, seed) -> None:
    seed()

    for role in ("admin", "employee"):
        response = client.post("/auth/demo-login", json={"role": role})
        assert response.status_code == 200
        assert response.json()["user"]["role"] == role

    # The seeded demo users can also use normal email + password login.
    password_login = client.post(
        "/auth/login", json={"email": "admin@coo.example", "password": TEST_PASSWORD}
    )
    assert password_login.status_code == 200


def test_demo_login_before_seeding_says_how_to_fix_it(client) -> None:
    response = client.post("/auth/demo-login", json={"role": "admin"})

    assert response.status_code == 503
    assert "app.seed" in response.json()["detail"]


def test_demo_login_is_off_unless_enabled(client, seed, test_settings) -> None:
    from app.config import get_settings

    seed()
    disabled = test_settings.model_copy(update={"demo_login_enabled": False})
    client.app.dependency_overrides[get_settings] = lambda: disabled

    assert client.post("/auth/demo-login", json={"role": "admin"}).status_code == 404


def test_permissions_are_enforced_on_the_backend(client, make_user, test_settings) -> None:
    @client.app.get("/_test/send-email")
    async def _send_email_probe(
        user: User = Depends(require_permission(Permission.EMAIL_SEND)),
    ) -> dict[str, bool]:
        return {"ok": True}

    employee = make_user("emp@coo.example", Role.EMPLOYEE)
    admin = make_user("adm@coo.example", Role.ADMIN)
    employee_token, _ = create_access_token(employee.id, test_settings)
    admin_token, _ = create_access_token(admin.id, test_settings)

    assert client.get("/_test/send-email").status_code == 401
    assert client.get("/_test/send-email", headers=bearer(employee_token)).status_code == 403
    assert client.get("/_test/send-email", headers=bearer(admin_token)).status_code == 200


def test_seed_creates_the_two_demo_users_once(seed, sync_engine) -> None:
    first_run = seed()
    second_run = seed()

    assert sorted(first_run) == ["admin@coo.example", "employee@coo.example"]
    assert second_run == []
    with Session(sync_engine) as session:
        users = session.exec(select(User)).all()
    assert {user.role for user in users} == {Role.ADMIN, Role.EMPLOYEE}
    assert all(user.password_hash.startswith("$argon2") for user in users)
