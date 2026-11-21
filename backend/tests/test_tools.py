"""Tool registry and specialists, without a database or a model."""

import asyncio
import json
import uuid

from app.agents.llm import ToolCall
from app.agents.specialists import ALL_SPECIALISTS, build_registry
from app.agents.tools import ToolContext, UserContext
from app.auth.permissions import permissions_for
from app.models import Role


def context_for(role: Role) -> ToolContext:
    email = "admin@coo.example" if role is Role.ADMIN else "employee@coo.example"
    user = UserContext(
        id=uuid.uuid4(), name="Test", email=email, role=role, permissions=permissions_for(role)
    )
    # These tools never touch the database, so no session is needed.
    return ToolContext(user=user, conversation_id=uuid.uuid4(), session=None)  # type: ignore[arg-type]


def run(name: str, arguments: dict, role: Role):
    registry = build_registry()
    return asyncio.run(registry.execute(ToolCall("toolu_1", name, arguments), context_for(role)))


def tool_names(role: Role) -> set[str]:
    return {d["name"] for d in build_registry().definitions_for(context_for(role).user)}


def test_employees_only_see_the_tools_they_may_use() -> None:
    employee, admin = tool_names(Role.EMPLOYEE), tool_names(Role.ADMIN)

    assert employee < admin
    assert {"mail_send_email", "mail_search_inbox", "hr_find_employee", "meetings_cancel", "tickets_update"} <= admin - employee
    assert {"web_get_news", "company_search_knowledge", "hr_request_leave", "tickets_list"} <= employee


def test_only_email_meetings_and_leave_need_approval() -> None:
    consequential = {t.name for s in ALL_SPECIALISTS for t in s.tools if t.consequential}

    assert consequential == {"mail_send_email", "meetings_cancel", "hr_request_leave"}


def test_executor_refuses_a_tool_the_user_may_not_use() -> None:
    # As if a prompt injection made the model call an admin tool for an employee.
    result = run("mail_send_email", {"to": ["Akshay"], "subject": "x", "body": "y"}, Role.EMPLOYEE)

    assert result.is_error
    assert "Permission denied" in json.loads(result.content)["error"]
    assert result.event["ok"] is False


def test_unknown_tool_and_invalid_input_are_errors_not_crashes() -> None:
    unknown = run("wire_money", {}, Role.ADMIN)
    invalid = run("hr_request_leave", {"start_date": "2026-12-10", "end_date": "2026-12-01"}, Role.ADMIN)

    assert unknown.is_error and "Unknown tool" in unknown.content
    assert invalid.is_error and "end_date is before start_date" in invalid.content


def test_results_are_marked_as_sample_data() -> None:
    stock = run("inventory_check_stock", {"item": "paper"}, Role.ADMIN)
    news = run("web_get_news", {}, Role.EMPLOYEE)

    assert json.loads(stock.content)["mock"] is True
    assert not news.is_error
    assert "mock" not in json.loads(news.content)  # live news, not sample data
    assert news.card["type"] == "headlines"


def test_unknown_or_ambiguous_recipient_asks_the_user() -> None:
    result = run("mail_send_email", {"to": ["Zed"], "subject": "Hi", "body": "Hello"}, Role.ADMIN)

    assert result.is_error
    assert "Ask the user" in json.loads(result.content)["error"]


def test_model_questions_are_dropped_before_the_read_back() -> None:
    from app.agents.turn import without_questions

    assert without_questions("I've drafted the email to Akshay. Should I send it?") == "I've drafted the email to Akshay."
    assert without_questions("Shall I go ahead?") == ""
    assert without_questions("Done. All set!") == "Done. All set!"
