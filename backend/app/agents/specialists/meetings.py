"""Meetings: handled by the central CoO agent itself (no separate specialist)."""

from datetime import date

from pydantic import BaseModel, Field

from app.agents import mock_data
from app.agents.tools import (
    Specialist,
    ToolContext,
    ToolError,
    ToolOutput,
    ToolSpec,
    awaiting_approval,
)
from app.auth.permissions import Permission


class ListMeetingsInput(BaseModel):
    day: date | None = Field(default=None, description="YYYY-MM-DD; defaults to today")


class CancelMeetingInput(BaseModel):
    meeting_id: str = Field(description="Id from list results, e.g. mtg-101")
    note: str | None = Field(default=None, max_length=300, description="Optional note to attendees")


async def list_meetings(ctx: ToolContext, args: ListMeetingsInput) -> ToolOutput:
    day = (args.day or mock_data.today()).isoformat()
    items = [m for m in mock_data.meetings() if m["start"].startswith(day)]
    return ToolOutput(data={"day": day, "meetings": items}, card={"type": "meetings", "items": items})


async def cancel_meeting(ctx: ToolContext, args: CancelMeetingInput) -> ToolOutput:
    meeting = next((m for m in mock_data.meetings() if m["id"] == args.meeting_id), None)
    if meeting is None:
        raise ToolError(f"No meeting with id {args.meeting_id}. List the meetings first.")
    start = meeting["start"].replace("T", " at ")
    action = await ctx.propose(
        action_type="meeting.cancel",
        description=f"Cancel '{meeting['title']}' on {start}, notifying {', '.join(meeting['attendees'])}",
        proposal={"meeting": meeting, "note": args.note},
    )
    return awaiting_approval(action)


MEETINGS = Specialist(
    name="coo",
    description="Calendar and meetings, handled by the central agent.",
    tools=(
        ToolSpec(
            name="meetings_list",
            description="List meetings on a day (today by default).",
            input_model=ListMeetingsInput,
            permission=Permission.MEETINGS_MANAGE,
            handler=list_meetings,
        ),
        ToolSpec(
            name="meetings_cancel",
            description="Propose cancelling a meeting. Creates a proposal that waits for the "
            "user's approval; nothing is cancelled yet.",
            input_model=CancelMeetingInput,
            permission=Permission.MEETINGS_MANAGE,
            handler=cancel_meeting,
            consequential=True,
        ),
    ),
)
