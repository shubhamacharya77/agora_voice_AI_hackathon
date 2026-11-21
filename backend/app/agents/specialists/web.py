"""Web specialist: current external information, live through Tavily."""

from datetime import datetime

from pydantic import BaseModel, Field

from app.agents.tools import Specialist, ToolContext, ToolError, ToolOutput, ToolSpec
from app.auth.permissions import Permission
from app.websearch import WebSearchError, web


class NewsInput(BaseModel):
    topic: str | None = Field(
        default=None, description="Optional topic, e.g. technology, business, India, cricket"
    )


class SearchInput(BaseModel):
    query: str = Field(min_length=2, description="What to look up on the web")


async def get_news(ctx: ToolContext, args: NewsInput) -> ToolOutput:
    topic = (args.topic or "").strip()
    try:
        results = await web().search(f"{topic} news" if topic else "top news headlines today", news=True)
    except WebSearchError as exc:
        raise ToolError(str(exc)) from exc
    headlines = [
        {"title": r["title"], "summary": r["snippet"], "url": r["url"]}
        | ({"published": r["published"]} if "published" in r else {})
        for r in results
    ]
    return ToolOutput(
        data={"date": datetime.now().astimezone().date().isoformat(), "topic": topic or None, "headlines": headlines},
        card={"type": "headlines", "items": headlines},
    )


async def search(ctx: ToolContext, args: SearchInput) -> ToolOutput:
    try:
        results = await web().search(args.query)
    except WebSearchError as exc:
        raise ToolError(str(exc)) from exc
    items = [{"title": r["title"], "snippet": r["snippet"], "url": r["url"]} for r in results]
    return ToolOutput(data={"query": args.query, "results": items}, card={"type": "links", "items": items})


WEB = Specialist(
    name="web",
    description="Current external information: news and web research.",
    tools=(
        ToolSpec(
            name="web_get_news",
            description="Get today's top news headlines with one-line summaries, optionally on a topic.",
            input_model=NewsInput,
            permission=Permission.WEB_SEARCH,
            handler=get_news,
            mock=False,
        ),
        ToolSpec(
            name="web_search",
            description="Search the web for current information on a topic.",
            input_model=SearchInput,
            permission=Permission.WEB_SEARCH,
            handler=search,
            mock=False,
        ),
    ),
)
