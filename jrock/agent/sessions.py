"""Conversation sessions: save, list, resume."""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "data" / "sessions"
ROOT.mkdir(parents=True, exist_ok=True)
MAX_RESUME_TURNS = 20


class SessionStore:
    def create(self, user_id: int, title: str = "session") -> str:
        sid = f"{int(time.time())}-{uuid.uuid4().hex[:6]}"
        (ROOT / f"{sid}.jsonl").touch()
        self._index(sid, user_id, title)
        return sid

    def _index_file(self) -> Path:
        return ROOT / "index.json"

    def _index(self, sid: str, user_id: int, title: str) -> dict:
        idx = {}
        if self._index_file().exists():
            try:
                idx = json.loads(self._index_file().read_text())
            except Exception:
                idx = {}
        idx[sid] = {"user_id": user_id, "title": title[:80], "created": time.time()}
        self._index_file().write_text(json.dumps(idx, indent=2))
        return idx

    def list(self, user_id: int) -> list[tuple[str, dict]]:
        if not self._index_file().exists():
            return []
        idx = json.loads(self._index_file().read_text())
        items = [(sid, meta) for sid, meta in idx.items() if meta["user_id"] == user_id]
        items.sort(key=lambda x: x[1]["created"], reverse=True)
        return items

    def append(self, sid: str, role: str, content: str) -> None:
        with (ROOT / f"{sid}.jsonl").open("a") as f:
            f.write(json.dumps({"role": role, "content": content, "t": time.time()}) + "\n")

    def load(self, sid: str, turns: int = MAX_RESUME_TURNS) -> list[dict]:
        path = ROOT / f"{sid}.jsonl"
        if not path.exists():
            return []
        msgs = []
        for line in path.read_text().splitlines():
            try:
                msgs.append(json.loads(line))
            except Exception:
                continue
        return [{"role": m["role"], "content": m["content"]} for m in msgs[-turns:]]

    def last_id(self, user_id: int) -> str | None:
        items = self.list(user_id)
        return items[0][0] if items else None
