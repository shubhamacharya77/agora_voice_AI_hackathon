"""The Tavily client and the web tools, against a fake Tavily (no network, no credits)."""

import asyncio
import json

import httpx2
import pytest

from app.config import Settings
from app.websearch import API, NotConfigured, TavilyClient, WebSearchError, configure

RESULT = {
    "title": "Rates held steady",
    "url": "https://news.example/rates",
    "content": "Policymakers   cited\neasing inflation. " + "More detail. " * 60,
    "score": 0.9,
    "published_date": "Sat, 21 Nov 2026 08:00:00 GMT",
}


def tavily(handler) -> TavilyClient:
    return TavilyClient("tvly-test", transport=httpx2.MockTransport(handler))


def test_news_asks_tavily_for_todays_news_and_shortens_snippets() -> None:
    requests: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(200, json={"results": [RESULT, {"title": "no url"}]})

    [item] = asyncio.run(tavily(handler).search("cricket news", news=True))

    body = json.loads(requests[0].content)
    assert str(requests[0].url) == API
    assert requests[0].headers["Authorization"] == "Bearer tvly-test"
    assert body == {"query": "cricket news", "topic": "news", "search_depth": "basic", "max_results": 5, "time_range": "day"}
    assert item["title"] == "Rates held steady" and item["url"] == "https://news.example/rates"
    assert item["snippet"].startswith("Policymakers cited easing inflation.")
    assert len(item["snippet"]) <= 301 and item["snippet"].endswith("…")
    assert item["published"] == RESULT["published_date"]


def test_a_quiet_news_day_widens_to_the_week() -> None:
    ranges: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        ranges.append(json.loads(request.content)["time_range"])
        return httpx2.Response(200, json={"results": [] if len(ranges) == 1 else [RESULT]})

    assert len(asyncio.run(tavily(handler).search("x news", news=True))) == 1
    assert ranges == ["day", "week"]


def test_general_search_has_no_time_limit() -> None:
    bodies: list[dict] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(request.content))
        return httpx2.Response(200, json={"results": [RESULT]})

    asyncio.run(tavily(handler).search("langgraph checkpoints"))

    assert bodies[0]["topic"] == "general" and "time_range" not in bodies[0]


@pytest.mark.parametrize(
    ("status", "message"),
    [(401, "rejected the API key"), (432, "usage limit"), (429, "rate"), (500, "answered 500")],
)
def test_failures_are_plain_sentences(status: int, message: str) -> None:
    client = tavily(lambda request: httpx2.Response(status, json={"detail": {"error": "nope"}}))

    with pytest.raises(WebSearchError, match=message):
        asyncio.run(client.search("anything"))


def test_unreachable_tavily_is_an_error_not_a_crash() -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("no route", request=request)

    with pytest.raises(WebSearchError, match="Couldn't reach web search"):
        asyncio.run(tavily(refuse).search("anything"))


def test_without_a_key_web_search_says_it_is_not_set_up() -> None:
    settings = Settings(_env_file=None, database_url="postgresql://x", jwt_secret="x" * 32, tavily_api_key="")

    assert isinstance(configure(settings), NotConfigured)
    with pytest.raises(WebSearchError, match="TAVILY_API_KEY"):
        asyncio.run(NotConfigured().search("anything"))


def test_news_tool_passes_the_topic_and_shows_live_headlines(fake_services) -> None:
    from test_tools import run

    from app.models import Role

    result = run("web_get_news", {"topic": "cricket"}, Role.EMPLOYEE)
    search = run("web_search", {"query": "Agora conversational AI"}, Role.EMPLOYEE)

    assert fake_services.queries == [("cricket news", True), ("Agora conversational AI", False)]
    data = json.loads(result.content)
    assert data["topic"] == "cricket" and "mock" not in data
    assert result.card["items"][0] == {
        "title": "Central bank holds interest rates steady",
        "summary": "Policymakers cited easing inflation and steady growth.",
        "url": "https://news.example/rates-steady",
        "published": "Sat, 21 Nov 2026 08:00:00 GMT",
    }
    assert search.card["type"] == "links" and search.card["items"][0]["url"] == "https://news.example/rates-steady"
