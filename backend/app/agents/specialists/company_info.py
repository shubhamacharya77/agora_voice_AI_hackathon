"""Company Info specialist: RAG over the company knowledge base.

Retrieval happens here, filtered by what the user may read; the central agent then
answers only from the passages it gets back (see the system prompt).
"""

from pydantic import BaseModel, Field

from app.agents.tools import Specialist, ToolContext, ToolOutput, ToolSpec
from app.auth.permissions import Permission
from app.knowledge import store as knowledge


class KnowledgeSearchInput(BaseModel):
    query: str = Field(
        min_length=2,
        max_length=300,
        description="The question or topic in plain words, e.g. 'carry over annual leave'",
    )


async def search_knowledge(ctx: ToolContext, args: KnowledgeSearchInput) -> ToolOutput:
    passages = await knowledge.search(
        ctx.session,
        args.query,
        include_restricted=Permission.KNOWLEDGE_READ_RESTRICTED in ctx.user.permissions,
    )
    items = [{"document": p.doc_title, "section": p.section, "text": p.content} for p in passages]
    card = (
        {
            "type": "documents",
            "items": [
                {"document": p.doc_title, "section": p.section, "excerpt": p.content[:280]}
                for p in passages
            ],
        }
        if passages
        else None
    )
    return ToolOutput(data={"query": args.query, "found": len(items), "passages": items}, card=card)


COMPANY_INFO = Specialist(
    name="company_info",
    description="Internal company knowledge: people, teams, offices, policies and processes.",
    tools=(
        ToolSpec(
            name="company_search_knowledge",
            description="Search the company knowledge base: company overview, leadership and "
            "teams, offices, policies (leave, holidays, remote work, expenses, travel, IT and "
            "security, conduct), payroll and benefits, help desk and onboarding. Returns the "
            "most relevant passages with their document names.",
            input_model=KnowledgeSearchInput,
            permission=Permission.KNOWLEDGE_READ,
            handler=search_knowledge,
        ),
    ),
)
