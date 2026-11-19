"""Query Brave Web Search for vacancy links on explicit user request."""
import html
import os

import httpx

from .discovery import _url
from .normalize import canonicalize_url


def find_links(role, location, *, transport=None):
    role = role.strip()
    query = " ".join(part for part in (role, location.strip(), "jobs vacancies careers") if part)
    if not role or len(query) > 400 or len(query.split()) > 50:
        raise ValueError("Enter a role and keep the search under 50 words and 400 characters.")
    key = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
    if not key:
        raise ValueError("Set BRAVE_SEARCH_API_KEY in your environment and restart Job Radar.")
    with httpx.Client(transport=transport, timeout=15, follow_redirects=False, trust_env=False) as client:
        try:
            response = client.get("https://api.search.brave.com/res/v1/web/search",
                                  params={"q": query, "count": 10, "text_decorations": "false"},
                                  headers={"Accept": "application/json", "X-Subscription-Token": key})
        except httpx.RequestError:
            raise ValueError("Could not reach Brave Search. Check your connection.") from None
    if response.status_code != 200:
        raise ValueError(f"Brave Search returned HTTP {response.status_code}. Check your key and search allowance.")
    data = response.json()
    if not isinstance(data, dict) or not isinstance(data.get("web", {}), dict):
        raise ValueError("Brave returned an unreadable results page.")
    rows = data.get("web", {}).get("results", [])
    if not isinstance(rows, list):
        raise ValueError("Brave returned an unreadable results list.")
    links = []
    seen = set()
    for row in rows[:10]:
        if not isinstance(row, dict):
            continue
        url = _url(row.get("url"))
        if not url:
            continue
        identity = canonicalize_url(url)
        if identity in seen:
            continue
        seen.add(identity)
        links.append((html.unescape(str(row.get("title") or url)), url))
    return links
