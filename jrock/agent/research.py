"""Deep search: plan sub-queries, search, fetch, synthesize with citations."""
from __future__ import annotations

import json
import re

from .context import AgentContext
from .tools import web


async def deep_research(query: str, ctx: AgentContext, rounds: int = 3) -> str:
    if not query.strip():
        return "Give me something to research."
    await ctx.say("🔎 Planning the research…")
    plan = await ctx.llm.chat(
        [{"role": "user",
          "content": "Break this research question into 3-5 specific web search "
                     f"queries. Return JSON list of strings.\n\nQuestion: {query}"}],
        temperature=0.2, max_tokens=400)
    raw = (plan.get("content") or "")
    subqueries = _parse_list(raw)
    if not subqueries:
        subqueries = [query]

    sources: list[tuple[str, str]] = []
    seen = set()
    for sq in subqueries:
        res = await web.search({"query": sq}, ctx)
        for url in re.findall(r"https?://\S+", res):
            url = url.rstrip(").,;")
            if url in seen:
                continue
            seen.add(url)
            sources.append((url, sq))
        if len(sources) >= 12:
            break
    top = sources[:12]
    if not top:
        return "No search results found."

    await ctx.say(f"📄 Reading {len(top)} sources…")
    docs = []
    for i, (url, sq) in enumerate(top, 1):
        text = await web.fetch({"url": url}, ctx)
        if len(text) > 4000:
            text = text[:4000]
        docs.append(f"<source n=\"{i}\" url=\"{url}\">\n{text}\n</source>")

    await ctx.say("✍️ Synthesising the answer…")
    prompt = (
        f"Research question: {query}\n\n"
        f"Sub-questions covered: {json.dumps(subqueries)}\n\n"
        "Sources:\n" + "\n\n".join(docs) +
        "\n\nWrite a thorough answer. Cite sources inline as [n] using the n of "
        "the source tag. If sources disagree, say so. If the sources do not "
        "answer the question, say what is missing.")
    msg = await ctx.llm.chat([{"role": "user", "content": prompt}],
                             temperature=0.3, max_tokens=3500)
    answer = msg.get("content") or "No answer generated."
    links = "\n".join(f"[{i}] {url}" for i, (url, _) in enumerate(top, 1))
    return f"{answer}\n\n<b>Sources</b>\n{links}"


def _parse_list(raw: str) -> list[str]:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    m = re.search(r"\[.*]", raw, re.S)
    if m:
        try:
            items = json.loads(m.group(0))
            return [str(x) for x in items][:5]
        except Exception:
            pass
    lines = [re.sub(r"^\s*[-*\d.)\s]+", "", l).strip()
             for l in raw.splitlines() if l.strip()]
    return [l for l in lines if 3 < len(l) < 120][:5]
