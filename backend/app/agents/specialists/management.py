"""Management specialist: office and ground operations, facilities, supplies."""

from typing import Literal

from pydantic import BaseModel, Field

from app.agents import mock_data
from app.agents.tools import Specialist, ToolContext, ToolOutput, ToolSpec
from app.auth.permissions import Permission


class ReportIssueInput(BaseModel):
    summary: str = Field(min_length=3, max_length=300, description="What's wrong")
    location: str | None = Field(default=None, description="Where, e.g. 'Room 2B'")
    urgency: Literal["low", "normal", "high"] = "normal"


class StockInput(BaseModel):
    item: str | None = Field(default=None, description="Item name; leave empty for everything")


async def report_issue(ctx: ToolContext, args: ReportIssueInput) -> ToolOutput:
    # Logging an issue changes nothing in the office, so it needs no approval.
    ticket = mock_data.create_ticket(
        summary=args.summary, location=args.location, urgency=args.urgency, reported_by=ctx.user.name
    )
    return ToolOutput(data={"ticket": ticket}, card={"type": "ticket", "item": ticket})


async def check_stock(ctx: ToolContext, args: StockInput) -> ToolOutput:
    needle = (args.item or "").lower()
    items = [i for i in mock_data.INVENTORY if needle in i["item"].lower()]
    low = [i["item"] for i in items if i["quantity"] <= i["reorder_at"]]
    return ToolOutput(data={"items": items, "low_stock": low}, card={"type": "inventory", "items": items})


MANAGEMENT = Specialist(
    name="management",
    description="Office and ground operations: facilities issues, supplies and inventory.",
    tools=(
        ToolSpec(
            name="facilities_report_issue",
            description="Log an office or facilities issue (broken equipment, cleaning, IT, access).",
            input_model=ReportIssueInput,
            permission=Permission.TICKET_CREATE,
            handler=report_issue,
        ),
        ToolSpec(
            name="inventory_check_stock",
            description="Check office supply levels and what needs reordering.",
            input_model=StockInput,
            permission=Permission.INVENTORY_MANAGE,
            handler=check_stock,
        ),
    ),
)
