"""Live web search and news through Tavily (https://tavily.com).

`configure(settings)` picks the client at startup. Without TAVILY_API_KEY the web tools
say web search isn't set up; they never invent results.
"""

import re
from typing import Any, Protocol

import httpx2

from app.config import Settings, secret

API = "https://api.tavily.com/search"
MAX_RESULTS = 5
# Snippets go to the model; keep them short (the free Groq tier counts every token).
SNIPPET_LIMIT = 300


class WebSearchError(Exception):
    """Search failed. Messages are plain sentences, safe to show and read aloud."""


class WebSearch(Protocol):
    async def search(self, query: str, *, news: bool = False) -> list[dict[str, Any]]: ...


class TavilyClient:
    """Tavily's search API. `transport` lets tests stand in for Tavily."""

    def __init__(
        self, api_key: str, *, transport: httpx2.AsyncBaseTransport | None = None, timeout: float = 15.0
    ) -> None:
        self._api_key = api_key
        self._transport = transport
        self._timeout = timeout

    async def _post(self, client: httpx2.AsyncClient, body: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            response = await client.post(API, json=body, headers={"Authorization": f"Bearer {self._api_key}"})
        except httpx2.HTTPError as exc:
            raise WebSearchError(f"Couldn't reach web search ({type(exc).__name__}).") from exc
        if response.status_code in (401, 403):
            raise WebSearchError("Web search rejected the API key. Check TAVILY_API_KEY.")
        if response.status_code in (429, 432, 433):
            raise WebSearchError("Web search is over its rate or usage limit right now. Try again later.")
        if response.status_code != 200:
            raise WebSearchError(f"Web search answered {response.status_code}.")
        return response.json().get("results", [])

    async def search(self, query: str, *, news: bool = False) -> list[dict[str, Any]]:
        """Up to five results: title, url, a short snippet and (for news) when it was published."""
        body: dict[str, Any] = {
            "query": query,
            "topic": "news" if news else "general",
            "search_depth": "basic",
            "max_results": MAX_RESULTS,
        }
        async with httpx2.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            results = await self._post(client, {**body, "time_range": "day"} if news else body)
            if news and not results:  # a quiet topic today: widen to the past week
                results = await self._post(client, {**body, "time_range": "week"})
        return [_result(r) for r in results[:MAX_RESULTS] if r.get("url")]


def _result(raw: dict[str, Any]) -> dict[str, Any]:
    snippet = re.sub(r"\s+", " ", str(raw.get("content") or "")).strip()
    if len(snippet) > SNIPPET_LIMIT:
        snippet = snippet[:SNIPPET_LIMIT].rsplit(" ", 1)[0] + "…"
    item = {"title": str(raw.get("title") or raw["url"]), "url": str(raw["url"]), "snippet": snippet}
    if raw.get("published_date"):
        item["published"] = str(raw["published_date"])
    return item


class NotConfigured:
    """No TAVILY_API_KEY: every search says so."""

    async def search(self, query: str, *, news: bool = False) -> list[dict[str, Any]]:
        raise WebSearchError("Web search isn't set up: TAVILY_API_KEY is missing in .env.")


_current: WebSearch = NotConfigured()


def web() -> WebSearch:
    return _current


def configure(settings: Settings) -> WebSearch:
    global _current
    key = secret(settings.tavily_api_key)
    _current = TavilyClient(key) if key else NotConfigured()
    return _current
