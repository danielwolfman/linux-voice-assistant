"""Simple web search tool for the Realtime satellite."""

from __future__ import annotations

import html
import logging
import re
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

import aiohttp

_LOGGER = logging.getLogger(__name__)

_ANCHOR_RE = re.compile(r'<a[^>]*class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>', re.DOTALL)
_SNIPPET_RE = re.compile(r'<a[^>]*class="result__snippet"[^>]*>(?P<snippet>.*?)</a>', re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")


class WebSearchTool:
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    def tool_definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": "web_search",
            "description": "Search the web for current or external information. Use this for internet knowledge, current events, public facts, or services outside Home Assistant.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to search for on the web."},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 8, "default": 5},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        }

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query", "")).strip()
        max_results = max(1, min(int(arguments.get("max_results", 5)), 8))
        _LOGGER.debug("Web search query=%r max_results=%s", query, max_results)
        if not query:
            return {"count": 0, "results": []}

        session = await self._session_or_create()
        url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
        async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=aiohttp.ClientTimeout(total=20)) as response:
            response.raise_for_status()
            page = await response.text()

        results = extract_duckduckgo_results(page, max_results=max_results)
        _LOGGER.debug("Web search results for %r: %s", query, [result["title"] for result in results])
        return {"count": len(results), "results": results}

    async def _session_or_create(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession()
        return self._session


def extract_duckduckgo_results(page: str, max_results: int) -> list[dict[str, str]]:
    anchors = list(_ANCHOR_RE.finditer(page))
    snippets = [clean_html(match.group("snippet")) for match in _SNIPPET_RE.finditer(page)]
    results: list[dict[str, str]] = []
    for index, match in enumerate(anchors[:max_results]):
        title = clean_html(match.group("title"))
        url = normalize_duckduckgo_url(match.group("href"))
        snippet = snippets[index] if index < len(snippets) else ""
        if title and url:
            results.append({"title": title, "url": url, "snippet": snippet})
    return results


def normalize_duckduckgo_url(url: str) -> str:
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        uddg = params.get("uddg", [])
        if uddg:
            return unquote(uddg[0])
        return "https://duckduckgo.com" + url
    return url


def clean_html(value: str) -> str:
    return html.unescape(_TAG_RE.sub("", value)).strip()
