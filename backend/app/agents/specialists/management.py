"""Management specialist: help-desk tickets (PostgreSQL), office supplies and inventory."""

import re
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlmodel import col, select

from app.agents import mock_data
from app.agents.tools import Specialist, ToolContext, ToolError, ToolOutput, ToolSpec
from app.auth.permissions import Permission
from app.models import Ticket, TicketCategory, TicketStatus, TicketUrgency
from app.models.base import utcnow

LIST_LIMIT = 10


class ReportIssueInput(BaseModel):
    summary: str = Field(min_length=3, max_length=300, description="What's wrong, in one line")
    category: TicketCategory = Field(
        default=TicketCategory.FACILITIES,
        description="it (laptops, Wi-Fi, VPN, projectors, accounts), facilities (AC, leaks, "
        "cleaning, furniture, printers), access (cards, doors), or other",
    )
    details: str = Field(default="", max_length=2000, description="Anything else that helps fix it")
    location: str | None = Field(default=None, description="Where, e.g. 'Bengaluru, Room 2B'")
    urgency: TicketUrgency = TicketUrgency.NORMAL


class ListTicketsInput(BaseModel):
    status: Literal["active", "open", "in_progress", "resolved", "all"] = Field(
        default="active", description="active = open or in progress"
    )
    category: TicketCategory | None = None
    mine_only: bool = Field(default=False, description="Only tickets the current user reported")


class UpdateTicketInput(BaseModel):
    ticket: str = Field(description="The ticket number, e.g. TCK-1003 or 1003")
    status: TicketStatus | None = None
    assigned_to: str | None = Field(default=None, max_length=200, description="Who should handle it")
    resolution_note: str | None = Field(default=None, max_length=1000, description="What was done")


class StockInput(BaseModel):
    item: str | None = Field(default=None, description="Item name; leave empty for everything")


def public(ticket: Ticket) -> dict[str, Any]:
    return {
        "id": ticket.code,
        "category": ticket.category.value,
        "summary": ticket.summary,
        "details": ticket.details,
        "location": ticket.location,
        "urgency": ticket.urgency.value,
        "status": ticket.status.value,
        "reported_by": ticket.reported_by_name,
        "assigned_to": ticket.assigned_to,
        "resolution_note": ticket.resolution_note,
        "created_at": ticket.created_at.isoformat(timespec="minutes"),
        "updated_at": ticket.updated_at.isoformat(timespec="minutes"),
    }


async def report_issue(ctx: ToolContext, args: ReportIssueInput) -> ToolOutput:
    # Logging an issue changes nothing in the office, so it needs no approval.
    ticket = Ticket(
        category=args.category,
        summary=" ".join(args.summary.split()),
        details=args.details.strip(),
        location=args.location,
        urgency=args.urgency,
        reported_by_name=ctx.user.name,
        reported_by_email=ctx.user.email,
    )
    ctx.session.add(ticket)
    await ctx.session.flush()  # assigns the ticket number; committed with the turn
    item = public(ticket)
    return ToolOutput(data={"ticket": item}, card={"type": "ticket", "item": item})


async def list_tickets(ctx: ToolContext, args: ListTicketsInput) -> ToolOutput:
    query = select(Ticket)
    # Employees only ever see the tickets they reported; admins see everyone's.
    if args.mine_only or Permission.TICKETS_MANAGE not in ctx.user.permissions:
        query = query.where(Ticket.reported_by_email == ctx.user.email)
    if args.status == "active":
        query = query.where(col(Ticket.status).in_([TicketStatus.OPEN, TicketStatus.IN_PROGRESS]))
    elif args.status != "all":
        query = query.where(Ticket.status == TicketStatus(args.status))
    if args.category:
        query = query.where(Ticket.category == args.category)
    tickets = (await ctx.session.exec(query.order_by(col(Ticket.created_at).desc()))).all()
    # Most urgent first, newest first within the same urgency.
    rank = {TicketUrgency.HIGH: 0, TicketUrgency.NORMAL: 1, TicketUrgency.LOW: 2}
    tickets = sorted(tickets, key=lambda t: rank[t.urgency])
    items = [public(t) for t in tickets[:LIST_LIMIT]]
    return ToolOutput(
        data={"tickets": items, "found": len(tickets), "shown": len(items)},
        card={"type": "tickets", "items": items},
    )


async def update_ticket(ctx: ToolContext, args: UpdateTicketInput) -> ToolOutput:
    match = re.fullmatch(r"\s*(?:TCK-?)?(\d{1,9})\s*", args.ticket, flags=re.IGNORECASE)
    ticket = await ctx.session.get(Ticket, int(match.group(1))) if match else None
    if ticket is None:
        raise ToolError(f"There's no ticket {args.ticket}.")
    if args.status is None and args.assigned_to is None and args.resolution_note is None:
        raise ToolError("Say what to change: the status, who it's assigned to, or a resolution note.")
    if args.status is not None:
        ticket.status = args.status
    if args.assigned_to is not None:
        ticket.assigned_to = args.assigned_to.strip() or None
    if args.resolution_note is not None:
        ticket.resolution_note = args.resolution_note.strip() or None
    ticket.updated_at = utcnow()
    ctx.session.add(ticket)
    await ctx.session.flush()
    item = public(ticket)
    return ToolOutput(data={"ticket": item, "updated": True}, card={"type": "ticket", "item": item})


async def check_stock(ctx: ToolContext, args: StockInput) -> ToolOutput:
    needle = (args.item or "").lower()
    items = [i for i in mock_data.INVENTORY if needle in i["item"].lower()]
    low = [i["item"] for i in items if i["quantity"] <= i["reorder_at"]]
    return ToolOutput(data={"items": items, "low_stock": low}, card={"type": "inventory", "items": items})


MANAGEMENT = Specialist(
    name="management",
    description="Office and ground operations: help-desk tickets (IT, facilities, access), "
    "supplies and inventory.",
    tools=(
        ToolSpec(
            name="facilities_report_issue",
            description="Log a help-desk ticket for an IT, facilities or access problem.",
            input_model=ReportIssueInput,
            permission=Permission.TICKET_CREATE,
            handler=report_issue,
            mock=False,
        ),
        ToolSpec(
            name="tickets_list",
            description="List help-desk tickets, most urgent first. Employees see the tickets "
            "they reported; admins see everyone's.",
            input_model=ListTicketsInput,
            permission=Permission.TICKET_CREATE,
            handler=list_tickets,
            mock=False,
        ),
        ToolSpec(
            name="tickets_update",
            description="Change a ticket's status, who it's assigned to, or its resolution note (admins only).",
            input_model=UpdateTicketInput,
            permission=Permission.TICKETS_MANAGE,
            handler=update_ticket,
            mock=False,
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
