"""Web specialist: current external information. Mock now; Tavily later."""

from urllib.parse import quote_plus

from pydantic import BaseModel, Field

from app.agents import mock_data
from app.agents.tools import Specialist, ToolContext, ToolOutput, ToolSpec
from app.auth.permissions import Permission


class NewsInput(BaseModel):
    topic: str | None = Field(
        default=None, description="Optional topic, e.g. technology, business, weather"
    )


class SearchInput(BaseModel):
    query: str = Field(min_length=2, description="What to look up on the web")


async def get_news(ctx: ToolContext, args: NewsInput) -> ToolOutput:
    topic = (args.topic or "").lower()
    items = [n for n in mock_data.NEWS if topic and topic in n["topic"]] or mock_data.NEWS
    headlines = [{"title": n["title"], "summary": n["summary"], "url": n["url"]} for n in items]
    return ToolOutput(
        data={"date": mock_data.today().isoformat(), "headlines": headlines},
        card={"type": "headlines", "items": headlines},
    )


async def search(ctx: ToolContext, args: SearchInput) -> ToolOutput:
    url = f"https://example.com/search?q={quote_plus(args.query)}"
    results = [
        {
            "title": f"Overview: {args.query}",
            "snippet": "Sample search result. Live web search is connected in a later phase.",
            "url": url,
        }
    ]
    return ToolOutput(data={"query": args.query, "results": results}, card={"type": "links", "items": results})


WEB = Specialist(
    name="web",
    description="Current external information: news and web research.",
    tools=(
        ToolSpec(
            name="web_get_news",
            description="Get today's top news headlines with one-line summaries.",
            input_model=NewsInput,
            permission=Permission.WEB_SEARCH,
            handler=get_news,
        ),
        ToolSpec(
            name="web_search",
            description="Search the web for current information on a topic.",
            input_model=SearchInput,
            permission=Permission.WEB_SEARCH,
            handler=search,
        ),
    ),
)
