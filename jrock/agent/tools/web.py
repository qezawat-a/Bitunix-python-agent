from __future__ import annotations

import html
import re
import urllib.parse

import httpx

from ..context import AgentContext

UA = {"User-Agent": "Mozilla/5.0 (compatible; JRockBot/1.0)"}
TAGS = re.compile(r"<script.*?</script>|<style.*?</style>", re.S | re.I)


def to_text(raw: str, limit: int = 8000) -> str:
    raw = TAGS.sub(" ", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw[:limit]


async def search(args: dict, ctx: AgentContext) -> str:
    q = urllib.parse.quote(args.get("query", ""))
    try:
        async with httpx.AsyncClient(timeout=25, headers=UA, follow_redirects=True) as c:
            r = await c.get(f"https://html.duckduckgo.com/html/?q={q}")
        links = re.findall(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r.text, re.S)
        out = []
        for href, title in links[:8]:
            title = to_text(title, 120)
            m = re.search(r"uddg=([^&]+)", href)
            if m:
                href = urllib.parse.unquote(m.group(1))
            out.append(f"- {title}\n  {href}")
        return "\n".join(out) or "No results."
    except Exception as e:
        return f"Search failed: {e}"


async def fetch(args: dict, ctx: AgentContext) -> str:
    url = args.get("url", "")
    try:
        async with httpx.AsyncClient(timeout=30, headers=UA, follow_redirects=True) as c:
            r = await c.get(url)
        ctype = r.headers.get("content-type", "")
        if "json" in ctype or "text" in ctype or "xml" in ctype:
            return r.text[:8000]
        return to_text(r.text)
    except Exception as e:
        return f"Fetch failed: {e}"


async def deepsearch(args: dict, ctx: AgentContext) -> str:
    from ..research import deep_research
    return await deep_research(args.get("query", ""), ctx)
