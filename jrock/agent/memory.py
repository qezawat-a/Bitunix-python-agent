"""Long-term memory + the /dream consolidation cycle."""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

DB = Path(__file__).resolve().parents[2] / "data" / "memory" / "memory.db"
DREAMS = Path(__file__).resolve().parents[2] / "data" / "memory" / "dreams"
DREAMS.mkdir(parents=True, exist_ok=True)
DB.parent.mkdir(parents=True, exist_ok=True)

STOP = set("the a an and or of to in is it for on with as at by be this that".split())


class Memory:
    def __init__(self) -> None:
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(DB)
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        with self._conn() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS facts(
                id INTEGER PRIMARY KEY, user_id INTEGER, fact TEXT,
                score REAL DEFAULT 1.0, created REAL, source TEXT)""")
            con.execute("""CREATE TABLE IF NOT EXISTS lessons(
                id INTEGER PRIMARY KEY, user_id INTEGER, lesson TEXT,
                hits INTEGER DEFAULT 0, created REAL)""")
            con.execute("""CREATE TABLE IF NOT EXISTS profile(
                user_id INTEGER PRIMARY KEY, data TEXT)""")

    # ------------------------------------------------------------- facts
    def store(self, user_id: int, fact: str, source: str = "chat") -> None:
        fact = fact.strip()
        if len(fact) < 4:
            return
        with self._conn() as con:
            con.execute("INSERT INTO facts(user_id,fact,created,source) VALUES(?,?,?,?)",
                        (user_id, fact, time.time(), source))

    def recall(self, user_id: int, query: str = "", k: int = 8) -> list[str]:
        q = {w for w in re.findall(r"\w+", query.lower()) if w not in STOP}
        rows = self._all(user_id)
        scored = []
        for r in rows:
            words = set(re.findall(r"\w+", r["fact"].lower()))
            overlap = len(q & words) / (len(q) + 1) if q else 0.0
            age_days = (time.time() - r["created"]) / 86400
            score = overlap + r["score"] - 0.01 * age_days
            if not q:
                score = r["score"] - 0.01 * age_days
            scored.append((score, r["fact"]))
        scored.sort(reverse=True)
        return [f for _, f in scored[:k]]

    def _all(self, user_id: int) -> list[sqlite3.Row]:
        with self._conn() as con:
            return list(con.execute("SELECT * FROM facts WHERE user_id=?", (user_id,)))

    def forget(self, user_id: int, fact_id: int) -> bool:
        with self._conn() as con:
            cur = con.execute("DELETE FROM facts WHERE id=? AND user_id=?", (fact_id, user_id))
            return cur.rowcount > 0

    def bump(self, user_id: int, fact: str) -> None:
        with self._conn() as con:
            con.execute("UPDATE facts SET score=score+0.5 WHERE user_id=? AND fact=?",
                        (user_id, fact))

    # ------------------------------------------------------------ lessons
    def add_lesson(self, user_id: int, lesson: str) -> None:
        with self._conn() as con:
            con.execute("INSERT INTO lessons(user_id,lesson,created) VALUES(?,?,?)",
                        (user_id, lesson.strip(), time.time()))

    def lessons(self, user_id: int, k: int = 10) -> list[str]:
        with self._conn() as con:
            rows = list(con.execute(
                "SELECT lesson FROM lessons WHERE user_id=? ORDER BY created DESC LIMIT ?",
                (user_id, k)))
        return [r["lesson"] for r in rows]

    # ------------------------------------------------------------ profile
    def get_profile(self, user_id: int) -> dict:
        with self._conn() as con:
            row = con.execute("SELECT data FROM profile WHERE user_id=?", (user_id,)).fetchone()
        return json.loads(row["data"]) if row else {}

    def set_profile(self, user_id: int, data: dict) -> None:
        with self._conn() as con:
            con.execute("INSERT OR REPLACE INTO profile(user_id,data) VALUES(?,?)",
                        (user_id, json.dumps(data)))

    # -------------------------------------------------------------- dream
    async def dream(self, user_id: int, llm) -> str:
        """Consolidate raw facts into insights + lessons (the /dream cycle)."""
        rows = self._all(user_id)
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
            self.add_lesson(user_id, lesson)
        if data.get("profile"):
            prof = self.get_profile(user_id)
            prof.update(data["profile"])
            self.set_profile(user_id, prof)
        # mark processed facts by lowering their score so they rank lower
        with self._conn() as con:
            con.execute("UPDATE facts SET score=score*0.5 WHERE user_id=?", (user_id,))
        stamp = time.strftime("%Y-%m-%d")
        out = DREAMS / f"{user_id}-{stamp}.md"
        out.write_text(f"# Dream {stamp}\n\n" +
                       "\n".join(f"- {i}" for i in data.get("insights", [])) +
                       "\n\n## Lessons\n" +
                       "\n".join(f"- {l}" for l in data.get("lessons", [])))
        self.add_lesson(user_id, f"Dream cycle {stamp}: {len(data.get('insights', []))} insights.")
        return (f"Dream complete. {len(data.get('insights', []))} insights, "
                f"{len(data.get('lessons', []))} lessons learned. Saved: {out.name}")
