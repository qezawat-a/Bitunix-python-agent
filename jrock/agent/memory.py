"""Long-term memory + the /dream consolidation cycle — stored in Neon."""
from __future__ import annotations

import json
import re
import time

from .. import store

STOP = set("the a an and or of to in is it for on with as at by be this that".split())


class Memory:
    """All methods are async and persist to Neon (tables: memory, lessons,
    memory_profile, kv). Schema is created by store.init_schema()."""

    # ------------------------------------------------------------- facts
    async def store(self, user_id: int, fact: str, source: str = "chat") -> None:
        fact = fact.strip()
        if len(fact) < 4:
            return
        now = int(time.time())
        await store.execute(
            "INSERT INTO memory(user_id,fact,source,created_at,updated_at) VALUES(?,?,?,?,?)",
            (user_id, fact, source, now, now),
        )

    async def recall(self, user_id: int, query: str = "", k: int = 8) -> list[str]:
        q = {w for w in re.findall(r"\w+", query.lower()) if w not in STOP}
        rows = await self._all(user_id)
        scored = []
        for r in rows:
            words = set(re.findall(r"\w+", r["fact"].lower()))
            overlap = len(q & words) / (len(q) + 1) if q else 0.0
            age_days = (time.time() - r["created_at"]) / 86400
            score = overlap + r["weight"] - 0.01 * age_days
            if not q:
                score = r["weight"] - 0.01 * age_days
            scored.append((score, r["fact"]))
        scored.sort(reverse=True)
        return [f for _, f in scored[:k]]

    async def _all(self, user_id: int) -> list[dict]:
        return await store.fetchall(
            "SELECT id,fact,weight,created_at FROM memory WHERE user_id=? ORDER BY id",
            (user_id,),
        )

    async def forget(self, user_id: int, fact_id: int) -> bool:
        n = await store.run("DELETE FROM memory WHERE id=? AND user_id=?", (fact_id, user_id))
        return n > 0

    async def bump(self, user_id: int, fact: str) -> None:
        await store.execute(
            "UPDATE memory SET weight=weight+0.5 WHERE user_id=? AND fact=?", (user_id, fact)
        )

    # ------------------------------------------------------------ lessons
    async def add_lesson(self, user_id: int, lesson: str) -> None:
        await store.execute(
            "INSERT INTO lessons(user_id,lesson,created_at) VALUES(?,?,?)",
            (user_id, lesson.strip(), int(time.time())),
        )

    async def lessons(self, user_id: int, k: int = 10) -> list[str]:
        rows = await store.fetchall(
            "SELECT lesson FROM lessons WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (user_id, k),
        )
        return [r["lesson"] for r in rows]

    # ------------------------------------------------------------ profile
    async def get_profile(self, user_id: int) -> dict:
        row = await store.fetchone(
            "SELECT data FROM memory_profile WHERE user_id=?", (user_id,)
        )
        return store.loads(row["data"], {}) if row else {}

    async def set_profile(self, user_id: int, data: dict) -> None:
        await store.execute(
            """INSERT INTO memory_profile(user_id,data,updated_at) VALUES(?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET data=EXCLUDED.data,
                                                  updated_at=EXCLUDED.updated_at""",
            (user_id, json.dumps(data), int(time.time())),
        )

    # -------------------------------------------------------------- dream
    async def dream(self, user_id: int, llm) -> str:
        """Consolidate raw facts into insights + lessons (the /dream cycle)."""
        rows = await self._all(user_id)
        if not rows:
            return "Nothing to dream about yet - no memories stored."
        facts = [r["fact"] for r in rows[-120:]]
        prompt = (
            "You are the dream engine of an AI agent. Consolidate these raw memories "
            "into insights about the user and how to serve them better.\n"
            "Return JSON: {\"insights\":[...],\"lessons\":[\"actionable behaviour rules\"],"
            "\"profile\":{\"name\":\"\",\"preferences\":[]}}\n\nMEMORIES:\n- "
            + "\n- ".join(facts))
        msg = await llm.chat([{"role": "user", "content": prompt}],
                             temperature=0.6, max_tokens=2000)
        raw = (msg.get("content") or "").strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
        try:
            data = json.loads(raw[raw.index("{"):raw.rindex("}") + 1])
        except Exception:
            data = {"insights": [raw[:1500]], "lessons": []}
        for lesson in data.get("lessons", [])[:10]:
            await self.add_lesson(user_id, lesson)
        if data.get("profile"):
            prof = await self.get_profile(user_id)
            prof.update(data["profile"])
            await self.set_profile(user_id, prof)
        # mark processed facts by lowering their score so they rank lower
        await store.execute("UPDATE memory SET weight=weight*0.5 WHERE user_id=?", (user_id,))
        stamp = time.strftime("%Y-%m-%d")
        name = f"dream:{user_id}:{stamp}"
        await store.kv_set(name, "# Dream " + stamp + "\n\n" +
                           "\n".join(f"- {i}" for i in data.get("insights", [])) +
                           "\n\n## Lessons\n" +
                           "\n".join(f"- {l}" for l in data.get("lessons", [])))
        await self.add_lesson(user_id, f"Dream cycle {stamp}: {len(data.get('insights', []))} insights.")
        return (f"Dream complete. {len(data.get('insights', []))} insights, "
                f"{len(data.get('lessons', []))} lessons learned. Saved: {name}")
