"""Company Info specialist: internal policies and documents, respecting who may read them."""

import re

from pydantic import BaseModel, Field

from app.agents import mock_data
from app.agents.tools import Specialist, ToolContext, ToolOutput, ToolSpec
from app.auth.permissions import Permission


class PolicySearchInput(BaseModel):
    query: str = Field(min_length=2, description="Topic to look up, e.g. leave, remote work, expenses")


async def search_policies(ctx: ToolContext, args: PolicySearchInput) -> ToolOutput:
    may_read_restricted = Permission.KNOWLEDGE_READ_RESTRICTED in ctx.user.permissions
    visible = [p for p in mock_data.POLICIES if may_read_restricted or not p["restricted"]]
    words = [w for w in re.findall(r"\w+", args.query.lower()) if len(w) > 2]
    matches = [
        p for p in visible if any(w in f"{p['title']} {p['text']}".lower() for w in words)
    ][:3]
    documents = [{"title": p["title"], "text": p["text"]} for p in matches]
    return ToolOutput(
        data={"query": args.query, "documents": documents, "found": len(documents)},
        card={"type": "documents", "items": documents} if documents else None,
    )


COMPANY_INFO = Specialist(
    name="company_info",
    description="Internal company knowledge: policies, processes and documents.",
    tools=(
        ToolSpec(
            name="company_search_policies",
            description="Search company policies and internal documents.",
            input_model=PolicySearchInput,
            permission=Permission.KNOWLEDGE_READ,
            handler=search_policies,
        ),
    ),
)
